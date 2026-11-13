import { CircleAlert, Plus, Trash2 } from 'lucide-react'
import { EWHA_AREAS, VISITS_MAX, VISITS_MIN, type Area } from '../../constants/recommend'
import { Badge, Button, Select } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'
import { newRowId, type PlaceRow } from './profileForm'

interface PlacesSectionProps {
  step: number
  rows: PlaceRow[]
  onRowsChange: (rows: PlaceRow[]) => void
}

// 자주 가는 곳 (통합안 1.1: 장소, 주간 횟수). 데이터 준비 중이라 저장만 하고 계산에 쓰지 않는다.
export function PlacesSection({ step, rows, onRowsChange }: PlacesSectionProps) {
  const update = (id: number, patch: Partial<PlaceRow>) =>
    onRowsChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const remove = (id: number) => onRowsChange(rows.filter((r) => r.id !== id))
  const add = () => onRowsChange([...rows, { id: newRowId(), area: EWHA_AREAS[0], visitsPerWeek: VISITS_MIN }])

  return (
    <SectionCard
      step={step}
      title={
        <span className="flex items-center gap-2">
          자주 가는 곳 (선택) <Badge tone="amber">데이터 준비 중</Badge>
        </span>
      }
      description="알바·헬스장·본가 가는 역 등 · 주 1회 이상 가는 곳"
    >
      <p className="flex items-start gap-2 rounded-control bg-amber-50 px-4 py-3 text-sm text-amber">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        지금은 매물에서 각 장소까지의 이동시간 데이터가 없어 추천 계산에 쓰지 않습니다(저장만 됩니다). 데이터가 준비되면 이동
        시간 점수에 반영됩니다.
      </p>

      <ul className="mt-4 flex flex-col gap-3">
        {rows.map((row, i) => {
          const label = `${i + 1}번째 장소`
          return (
            <li key={row.id} className="flex flex-wrap items-end gap-4 rounded-control border border-border p-4">
              <label className="flex w-40 flex-col gap-1.5 text-xs text-muted">
                장소
                <Select value={row.area} onChange={(e) => update(row.id, { area: e.target.value as Area })}>
                  {EWHA_AREAS.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </Select>
              </label>
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
            </li>
          )
        })}
      </ul>

      <Button variant="secondary" size="sm" className="mt-3" onClick={add}>
        <Plus className="size-4" aria-hidden />
        장소 추가
      </Button>
    </SectionCard>
  )
}
