import { Building2 } from 'lucide-react'
import { IMPORTANCE_MAX, IMPORTANCE_MIN, VISITS_MAX, VISITS_MIN } from '../../constants/recommend'
import { cx } from '../../utils/cx'
import type { SpotVisits } from '../../utils/walk'
import { EmptyState } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'

const LEVELS = Array.from({ length: IMPORTANCE_MAX - IMPORTANCE_MIN + 1 }, (_, i) => IMPORTANCE_MIN + i)

function ImportancePicker({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium text-text">{label}</legend>
      <div className="inline-flex overflow-hidden rounded-control border border-border" role="radiogroup">
        {LEVELS.map((n) => (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            aria-label={`${label} ${n}`}
            onClick={() => onChange(n)}
            className={cx(
              'h-9 w-11 border-l border-border text-sm first:border-l-0',
              value === n ? 'bg-blue-50 font-bold text-blue' : 'bg-white text-text hover:bg-bg',
            )}
          >
            {n}
          </button>
        ))}
      </div>
      <p className="mt-1 text-xs text-muted">1 덜 중요 · 5 매우 중요</p>
    </fieldset>
  )
}

interface SimpleSettingsSectionProps {
  loading: boolean
  universityLabel: string
  spotNames: string[]
  visits: SpotVisits
  onVisitsChange: (name: string, count: number | null) => void
  costImportance: number
  walkImportance: number
  onCostImportanceChange: (v: number) => void
  onWalkImportanceChange: (v: number) => void
}

export function SimpleSettingsSection({
  loading,
  universityLabel,
  spotNames,
  visits,
  onVisitsChange,
  costImportance,
  walkImportance,
  onCostImportanceChange,
  onWalkImportanceChange,
}: SimpleSettingsSectionProps) {
  return (
    <SectionCard
      step={5}
      title="간단 추천 설정"
      description="시간표를 입력하지 않았을 때 쓰는 기준 · 강의동별 주간 방문 횟수와 중요도(1~5)"
    >
      {loading ? (
        <p className="py-8 text-center text-sm text-muted">강의동 정보를 불러오는 중입니다…</p>
      ) : spotNames.length === 0 ? (
        <EmptyState
          icon={Building2}
          title={`${universityLabel} 강의동 정보가 아직 없습니다`}
          description="현재는 이화여대 강의동만 제공합니다."
        />
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {spotNames.map((name) => {
            const count = visits[name]
            const selected = count !== undefined
            return (
              <li
                key={name}
                className="flex items-center justify-between gap-3 rounded-control border border-border px-4 py-3"
              >
                <label className="flex min-w-0 items-center gap-2 text-sm font-medium text-text">
                  <input
                    type="checkbox"
                    className="size-4 accent-blue"
                    checked={selected}
                    onChange={(e) => onVisitsChange(name, e.target.checked ? VISITS_MIN : null)}
                  />
                  <span className="truncate">{name}</span>
                </label>
                <Stepper
                  label={`${name} 주간 방문 횟수`}
                  value={count ?? 0}
                  min={VISITS_MIN}
                  max={VISITS_MAX}
                  unit="회"
                  disabled={!selected}
                  onChange={(v) => onVisitsChange(name, v)}
                />
              </li>
            )
          })}
        </ul>
      )}

      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <ImportancePicker label="주거비 중요도" value={costImportance} onChange={onCostImportanceChange} />
        <ImportancePicker label="이동시간 중요도" value={walkImportance} onChange={onWalkImportanceChange} />
      </div>
    </SectionCard>
  )
}
