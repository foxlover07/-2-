import { Building2 } from 'lucide-react'
import { VISITS_MAX, VISITS_MIN } from '../../constants/recommend'
import type { SpotVisits } from '../../utils/walk'
import { EmptyState } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'

interface SimpleSettingsSectionProps {
  step: number
  loading: boolean
  universityLabel: string
  spotNames: string[]
  visits: SpotVisits
  onVisitsChange: (name: string, count: number | null) => void
}

// 강의동별 주간 방문 횟수. 추천 점수에는 쓰지 않고, 매물 상세·비교 화면의 가중 평균 도보시간 표시에만 쓴다.
export function SimpleSettingsSection({
  step,
  loading,
  universityLabel,
  spotNames,
  visits,
  onVisitsChange,
}: SimpleSettingsSectionProps) {
  return (
    <SectionCard
      step={step}
      title="강의동 방문 횟수 (선택)"
      description="매물 상세·비교 화면의 가중 평균 도보시간 표시에 쓰입니다 · 추천 점수에는 들어가지 않습니다"
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
    </SectionCard>
  )
}
