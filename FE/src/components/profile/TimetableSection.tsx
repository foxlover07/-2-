import { CalendarDays, Plus, Trash2, Upload } from 'lucide-react'
import { DAYS, type Day } from '../../constants/recommend'
import { cx } from '../../utils/cx'
import { Badge, Button, Select } from '../common'
import { SectionCard } from './SectionCard'
import { TIME_INPUT, newRowId, type TimetableRow } from './profileForm'

interface TimetableSectionProps {
  step: number
  rows: TimetableRow[]
  onRowsChange: (rows: TimetableRow[]) => void
  rowErrors: Record<number, string>
  buildings: string[]
  // 건물별 안내 (예: 일부 매물만 도보시간이 있는 건물)
  buildingNotes?: Record<string, string>
}

export function TimetableSection({ step, rows, onRowsChange, rowErrors, buildings, buildingNotes = {} }: TimetableSectionProps) {
  const update = (id: number, patch: Partial<TimetableRow>) =>
    onRowsChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const remove = (id: number) => onRowsChange(rows.filter((r) => r.id !== id))
  const add = () => {
    const last = rows.at(-1)
    onRowsChange([...rows, { id: newRowId(), day: last?.day ?? '월', start: '', end: '', building: '' }])
  }
  const dayCount = new Set(rows.map((r) => r.day)).size

  return (
    <SectionCard
      step={step}
      title="시간표"
      description="주간 통학·공강 귀가·1교시 계산에 쓰입니다 · 없으면 주간 통학만 학과 건물로 추정하고, 공강·1교시는 계산하지 않습니다"
    >
      {/* 캡처 불러오기: Vision LLM 연결 전이라 자리만 둔다 */}
      <div
        className="flex items-center gap-3 rounded-control border border-dashed border-border bg-bg/50 px-4 py-3 text-muted"
        aria-disabled
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-control border border-border bg-white">
          <Upload className="size-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium text-text">
            시간표 캡처로 불러오기 <Badge>준비 중</Badge>
          </p>
          <p className="text-xs">캡처를 올리면 자동 인식 후, 확인 단계를 거쳐 아래 표에 채워집니다.</p>
        </div>
      </div>

      {/* relative: 표 안의 sr-only(absolute) 요소가 스크롤 영역 밖으로 나가 모바일에서 페이지가 가로로 넓어지지 않게 */}
      <div className="relative mt-4 overflow-x-auto rounded-control border border-border">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="bg-bg text-left text-xs text-muted">
            <tr>
              <th className="w-24 px-3 py-2 font-medium">요일</th>
              <th className="w-32 px-2 py-2 font-medium">시작</th>
              <th className="w-32 px-2 py-2 font-medium">종료</th>
              <th className="px-2 py-2 font-medium">강의 건물</th>
              <th className="w-12 px-2 py-2">
                <span className="sr-only">삭제</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-sm text-muted">
                  아직 입력한 수업이 없습니다. 시간표가 없으면 주간 통학만 학과 건물로 추정합니다.
                </td>
              </tr>
            )}
            {rows.map((row, i) => {
              const error = rowErrors[row.id]
              const label = `${i + 1}번째 수업`
              return (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="px-3 py-2">
                    <Select
                      aria-label={`${label} 요일`}
                      value={row.day}
                      onChange={(e) => update(row.id, { day: e.target.value as Day })}
                    >
                      {DAYS.map((d) => (
                        <option key={d} value={d}>
                          {d}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-2 py-2">
                    <input
                      type="time"
                      aria-label={`${label} 시작 시각`}
                      className={cx(TIME_INPUT, error ? 'border-red' : 'border-border')}
                      value={row.start}
                      onChange={(e) => update(row.id, { start: e.target.value })}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <input
                      type="time"
                      aria-label={`${label} 종료 시각`}
                      className={cx(TIME_INPUT, error ? 'border-red' : 'border-border')}
                      value={row.end}
                      onChange={(e) => update(row.id, { end: e.target.value })}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <Select
                      aria-label={`${label} 강의 건물`}
                      value={row.building}
                      invalid={Boolean(error) && (row.building === '' || !buildings.includes(row.building))}
                      onChange={(e) => update(row.id, { building: e.target.value })}
                    >
                      <option value="">건물 선택</option>
                      {row.building !== '' && !buildings.includes(row.building) && (
                        <option value={row.building}>{row.building} (목록에 없음)</option>
                      )}
                      {buildings.map((b) => (
                        <option key={b} value={b}>
                          {buildingNotes[b] ? `${b} (${buildingNotes[b]})` : b}
                        </option>
                      ))}
                    </Select>
                    {error && (
                      <p role="alert" className="mt-1 text-xs text-red">
                        {error}
                      </p>
                    )}
                  </td>
                  <td className="px-2 py-2">
                    <button
                      type="button"
                      onClick={() => remove(row.id)}
                      aria-label={`${label} 삭제`}
                      className="flex size-10 items-center justify-center rounded-control text-muted hover:bg-bg hover:text-text"
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <Button variant="secondary" size="sm" onClick={add}>
          <Plus className="size-4" aria-hidden />
          수업 추가
        </Button>
        <p className="flex items-center gap-1 text-xs text-muted">
          <CalendarDays className="size-3.5" aria-hidden />주 {dayCount}일 · 수업 {rows.length}개
        </p>
      </div>
      {Object.keys(buildingNotes).length > 0 && (
        <p className="mt-2 text-xs text-muted">
          괄호 안 숫자는 도보시간이 계산된 매물 수입니다. 일부 매물만 계산된 건물을 고르면, 그 건물까지 도보시간이 없는 매물은
          추천에서 빠집니다 (계산은 순서대로 늘리는 중).
        </p>
      )}
    </SectionCard>
  )
}
