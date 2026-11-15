import { CalendarDays, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { DAYS, type Day } from '../../constants/recommend'
import { PERIODS, groupConsecutive, periodRangeLabel, toPeriodRange } from '../../constants/periods'
import { useUniversity } from '../../contexts/UniversityContext'
import { Button, Chip, Select } from '../common'
import { SectionCard } from './SectionCard'
import { TimetableImageImport } from './TimetableImageImport'
import { WeekGrid } from './WeekGrid'
import { newRowId, type TimetableRow } from './profileForm'

// 시간표는 평일 수업만 받는다
const WEEKDAYS = DAYS.slice(0, 5) as readonly Day[]

interface TimetableSectionProps {
  step: number
  rows: TimetableRow[]
  onRowsChange: (rows: TimetableRow[]) => void
  rowErrors: Record<number, string>
  buildings: string[]
  // 건물별 안내 (예: 일부 매물만 도보시간이 있는 건물)
  buildingNotes?: Record<string, string>
  university?: string // 사진 입력에서 건물 이름을 맞출 대학 (없으면 선택한 대학)
}

// 화면에 보이는 한 줄 = 같은 건물·같은 시간대의 수업들 (요일만 다름). 저장은 요일마다 한 행이다.
interface ClassGroup {
  key: string
  days: Day[]
  label: string // "3–4교시" 또는 교시와 안 맞는 예전 입력이면 "13:00–14:15"
  building: string
  rowIds: number[]
  error: string | null
}

const overlaps = (a: { start: string; end: string }, b: { start: string; end: string }) =>
  a.start < b.end && b.start < a.end

export function TimetableSection({
  step,
  rows,
  onRowsChange,
  rowErrors,
  buildings,
  buildingNotes = {},
  university: universityProp,
}: TimetableSectionProps) {
  const { university: selectedUniversity } = useUniversity()
  const university = (universityProp ?? selectedUniversity) as typeof selectedUniversity
  const periods = PERIODS[university]

  const [editing, setEditing] = useState(false)
  const [days, setDays] = useState<Day[]>([])
  const [periodNos, setPeriodNos] = useState<number[]>([])
  const [building, setBuilding] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // 대학을 바꾸면 교시·건물 기준이 달라지므로 입력한 시간표를 비운다 (처음 그릴 때는 그대로 둔다)
  const prevUniversity = useRef(university)
  useEffect(() => {
    if (prevUniversity.current === university) return
    prevUniversity.current = university
    resetEditor()
    if (rows.length > 0) {
      onRowsChange([])
      setNotice('대학이 바뀌어 시간표를 비웠습니다. 다시 입력해 주세요.')
    }
    // rows·onRowsChange 는 대학이 바뀔 때만 보면 된다 (의존성에 넣지 않음)
  }, [university])

  const groups = useMemo<ClassGroup[]>(() => {
    const map = new Map<string, ClassGroup>()
    for (const r of rows) {
      const key = `${r.building}|${r.start}|${r.end}`
      const range = toPeriodRange(periods, r.start, r.end)
      const g = map.get(key) ?? {
        key,
        days: [],
        label: range ? periodRangeLabel(range) : `${r.start}–${r.end}`,
        building: r.building,
        rowIds: [],
        error: null,
      }
      g.days.push(r.day)
      g.rowIds.push(r.id)
      g.error ??= rowErrors[r.id] ?? null
      map.set(key, g)
    }
    const dayIndex = (d: Day) => DAYS.indexOf(d)
    return [...map.values()]
      .map((g) => ({ ...g, days: [...g.days].sort((a, b) => dayIndex(a) - dayIndex(b)) }))
      .sort((a, b) => dayIndex(a.days[0]) - dayIndex(b.days[0]) || a.key.localeCompare(b.key))
  }, [rows, rowErrors, periods])

  function resetEditor() {
    setEditing(false)
    setDays([])
    setPeriodNos([])
    setBuilding('')
    setAddError(null)
  }

  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  function add() {
    if (days.length === 0) return setAddError('요일을 하나 이상 골라 주세요.')
    if (periodNos.length === 0) return setAddError('교시를 하나 이상 골라 주세요.')
    if (building === '') return setAddError('강의 건물을 골라 주세요.')

    // 떨어진 교시(1, 3)는 따로, 붙은 교시(3, 4)는 한 수업으로 저장한다
    const spans = groupConsecutive(periodNos).map((g) => {
      const first = periods.find((x) => x.no === g[0])!
      const last = periods.find((x) => x.no === g.at(-1))!
      return { start: first.start, end: last.end }
    })
    const newRows: TimetableRow[] = days.flatMap((day) =>
      spans.map((s) => ({ id: newRowId(), day, start: s.start, end: s.end, building })),
    )

    const clash = newRows.find((n) => rows.some((r) => r.day === n.day && overlaps(r, n)))
    if (clash) {
      const other = rows.find((r) => r.day === clash.day && overlaps(r, clash))!
      return setAddError(`${clash.day}요일 그 시간에 이미 ${other.building} 수업이 있습니다.`)
    }

    onRowsChange([...rows, ...newRows])
    setNotice(null)
    resetEditor()
  }

  const remove = (ids: number[]) => onRowsChange(rows.filter((r) => !ids.includes(r.id)))
  const dayCount = new Set(rows.map((r) => r.day)).size

  return (
    <SectionCard
      step={step}
      title="시간표"
      description="선택 · 요일마다 첫 수업 건물과 마지막 수업 건물까지 편도 시간을 이동 시간에 더합니다 (이동 시간의 20%)"
    >
      {notice && (
        <p role="status" className="mb-3 rounded-control bg-bg px-3 py-2 text-sm text-text">
          {notice}
        </p>
      )}

      {/* 캡처 불러오기: 사진을 읽고(OCR) → 확인·수정 → 표에 추가 (feat/decision-support) */}
      <div className="mb-3">
        <TimetableImageImport university={university} onAdd={(added) => onRowsChange([...rows, ...added])} />
      </div>

      {/* 에타 느낌의 주간 미리보기 (건물별 색) */}
      <div className="mb-3">
        <WeekGrid periods={periods} slots={rows} minRows={periods.length > 10 ? 9 : 6} />
      </div>

      {/* 추가한 수업 목록 */}
      <ul className="divide-y divide-border rounded-control border border-border">
        {groups.length === 0 && (
          <li className="px-3 py-6 text-center text-sm text-muted">
            아직 추가한 수업이 없습니다. 시간표가 없으면 이동 시간은 수업 듣는 건물만으로 계산합니다.
          </li>
        )}
        {groups.map((g) => (
          <li key={g.key} className="flex items-start gap-3 px-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="text-sm text-text">
                <span className="font-medium">{g.days.join('·')}</span>
                <span className="mx-2 text-muted">|</span>
                {g.label}
                <span className="mx-2 text-muted">|</span>
                {g.building}
              </p>
              {g.error && (
                <p role="alert" className="mt-1 text-xs text-red">
                  {g.error}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={() => remove(g.rowIds)}
              aria-label={`${g.days.join('·')} ${g.label} ${g.building} 수업 삭제`}
              className="flex size-9 shrink-0 items-center justify-center rounded-control text-muted hover:bg-bg hover:text-text"
            >
              <Trash2 className="size-4" aria-hidden />
            </button>
          </li>
        ))}
      </ul>

      {/* 수업 추가 */}
      {editing ? (
        <div className="mt-3 space-y-4 rounded-control border border-border bg-bg/50 p-4">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-text">수업 추가</p>
            <button
              type="button"
              onClick={resetEditor}
              aria-label="수업 추가 닫기"
              className="flex size-8 items-center justify-center rounded-control text-muted hover:bg-white hover:text-text"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>

          <fieldset>
            <legend className="mb-2 text-xs text-muted">요일 · 여러 개 고를 수 있습니다</legend>
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((d) => (
                <Chip key={d} selected={days.includes(d)} onClick={() => setDays(toggle(days, d))}>
                  {d}
                </Chip>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend className="mb-2 text-xs text-muted">교시 · 여러 개 고를 수 있습니다 (붙은 교시는 한 수업으로 묶입니다)</legend>
            <div className="flex flex-wrap gap-2">
              {periods.map((x) => (
                <Chip
                  key={x.no}
                  selected={periodNos.includes(x.no)}
                  onClick={() => setPeriodNos(toggle(periodNos, x.no))}
                  aria-label={`${x.no}교시 ${x.start}부터 ${x.end}까지`}
                >
                  {x.no}교시 <span className="text-xs text-muted">{x.start}</span>
                </Chip>
              ))}
            </div>
          </fieldset>

          <div>
            <label htmlFor="tt-building" className="mb-2 block text-xs text-muted">
              강의 건물
            </label>
            <Select id="tt-building" value={building} onChange={(e) => setBuilding(e.target.value)}>
              <option value="">건물 선택</option>
              {buildings.map((b) => (
                <option key={b} value={b}>
                  {buildingNotes[b] ? `${b} (${buildingNotes[b]})` : b}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-muted">목록에 없는 건물이면 가장 가까운 건물을 골라 주세요.</p>
          </div>

          {addError && (
            <p role="alert" className="text-xs text-red">
              {addError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={resetEditor}>
              취소
            </Button>
            <Button size="sm" onClick={add}>
              추가
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            <Plus className="size-4" aria-hidden />
            추가하기
          </Button>
          <p className="flex items-center gap-1 text-xs text-muted">
            <CalendarDays className="size-3.5" aria-hidden />주 {dayCount}일 · 수업 {rows.length}개
          </p>
        </div>
      )}

      {Object.keys(buildingNotes).length > 0 && (
        <p className="mt-2 text-xs text-muted">
          괄호 안 숫자는 도보시간이 계산된 매물 수입니다. 일부 매물만 계산된 건물을 고르면, 그 건물까지 도보시간이 없는 매물은
          추천에서 빠집니다 (계산은 순서대로 늘리는 중).
        </p>
      )}
    </SectionCard>
  )
}
