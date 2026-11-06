import { CircleAlert, Moon, Plus, Trash2 } from 'lucide-react'
import {
  EWHA_AREAS,
  LATE_NIGHT_FROM,
  PLACE_KINDS,
  TIME_RE,
  VISITS_MAX,
  VISITS_MIN,
  isLateNight,
  type Area,
} from '../../constants/recommend'
import { Badge, Button, Chip, Select } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'
import { TIME_INPUT, newRowId, type PlaceRow } from './profileForm'

interface PlacesSectionProps {
  rows: PlaceRow[]
  onRowsChange: (rows: PlaceRow[]) => void
}

export function PlacesSection({ rows, onRowsChange }: PlacesSectionProps) {
  const update = (id: number, patch: Partial<PlaceRow>) =>
    onRowsChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const remove = (id: number) => onRowsChange(rows.filter((r) => r.id !== id))
  const add = () =>
    onRowsChange([
      ...rows,
      { id: newRowId(), area: EWHA_AREAS[0], kind: 'work', visitsPerWeek: VISITS_MIN, fromCampus: false, endsAt: '' },
    ])

  return (
    <SectionCard step={2} title="자주 가는 곳" description="알바·헬스장·공부·본가 가는 역 등 · 주 1회 이상 가는 곳">
      <p className="flex items-start gap-2 rounded-control bg-amber-50 px-4 py-3 text-sm text-amber">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        지금은 매물에서 각 지역까지의 이동시간 데이터가 없어 추천 계산에서 빠집니다. 데이터가 준비되면 자동으로
        반영됩니다.
      </p>

      <ul className="mt-4 flex flex-col gap-3">
        {rows.map((row, i) => {
          const label = `${i + 1}번째 장소`
          const late = TIME_RE.test(row.endsAt) && isLateNight(row.endsAt)
          return (
            <li key={row.id} className="rounded-control border border-border p-4">
              <div className="flex flex-wrap items-end gap-4">
                <label className="flex w-40 flex-col gap-1.5 text-xs text-muted">
                  지역
                  <Select value={row.area} onChange={(e) => update(row.id, { area: e.target.value as Area })}>
                    {EWHA_AREAS.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </Select>
                </label>
                <fieldset className="flex flex-col gap-1.5">
                  <legend className="mb-1.5 text-xs text-muted">용도</legend>
                  <div className="flex flex-wrap gap-1.5">
                    {PLACE_KINDS.map((k) => (
                      <Chip
                        key={k.value}
                        selected={row.kind === k.value}
                        onClick={() => update(row.id, { kind: k.value })}
                        className="h-8 px-3"
                      >
                        {k.label}
                      </Chip>
                    ))}
                  </div>
                </fieldset>
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs text-muted">주간 횟수</span>
                  <Stepper
                    label={`${label} 주간 횟수`}
                    value={row.visitsPerWeek}
                    min={VISITS_MIN}
                    max={VISITS_MAX}
                    unit="회"
                    onChange={(v) => update(row.id, { visitsPerWeek: v })}
                  />
                </div>
                <button
                  type="button"
                  onClick={() => remove(row.id)}
                  aria-label={`${label} 삭제`}
                  className="ml-auto flex size-10 items-center justify-center rounded-control text-muted hover:bg-bg hover:text-text"
                >
                  <Trash2 className="size-4" aria-hidden />
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <label className="flex items-center gap-2 text-sm text-text">
                  <input
                    type="checkbox"
                    className="size-4 accent-blue"
                    checked={row.fromCampus}
                    onChange={(e) => update(row.id, { fromCampus: e.target.checked })}
                  />
                  수업 끝나고 학교에서 바로 가요
                  <span className="text-xs text-muted">(장소 → 집 편도만 계산)</span>
                </label>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">끝나는 시각 (선택)</span>
                  <input
                    type="time"
                    aria-label={`${label} 끝나는 시각`}
                    className={`${TIME_INPUT} w-32 border-border`}
                    value={row.endsAt}
                    onChange={(e) => update(row.id, { endsAt: e.target.value })}
                  />
                  {late && (
                    <Badge tone="navy">
                      <Moon className="size-3" aria-hidden />
                      심야 일정
                    </Badge>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ul>

      <Button variant="secondary" size="sm" className="mt-3" onClick={add}>
        <Plus className="size-4" aria-hidden />
        장소 추가
      </Button>
      <p className="mt-3 text-xs text-muted">
        지역: {EWHA_AREAS.join(' · ')} | {LATE_NIGHT_FROM} 이후 끝나면 심야 귀가 기준에 쓰입니다
      </p>
    </SectionCard>
  )
}
