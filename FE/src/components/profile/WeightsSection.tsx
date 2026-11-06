import {
  CircleCheck,
  CircleAlert,
  Clock,
  CreditCard,
  House,
  Info,
  MapPin,
  Moon,
  SlidersHorizontal,
  type LucideIcon,
} from 'lucide-react'
import {
  CRITERIA,
  DATA_PENDING_CRITERIA,
  DISABLED_CRITERIA,
  EQUAL_WEIGHTS,
  WEIGHTS_TOTAL,
  type CriterionKey,
  type WeightsPercent,
} from '../../constants/recommend'
import { cx } from '../../utils/cx'
import { Badge, Button } from '../common'
import { SectionCard } from './SectionCard'

const ICON: Record<CriterionKey, LucideIcon> = {
  monthly_cost: CreditCard,
  weekly_commute: Clock,
  gap_home: House,
  frequent_place: MapPin,
  late_return: Moon,
  preference_match: SlidersHorizontal,
}

interface WeightsSectionProps {
  weights: WeightsPercent
  sum: number
  onChange: (weights: WeightsPercent) => void
}

function clampPercent(n: number): number {
  if (!Number.isFinite(n)) return 0
  return Math.min(WEIGHTS_TOTAL, Math.max(0, Math.round(n)))
}

export function WeightsSection({ weights, sum, onChange }: WeightsSectionProps) {
  const set = (key: CriterionKey, value: number) => onChange({ ...weights, [key]: clampPercent(value) })
  const ok = sum === WEIGHTS_TOTAL

  return (
    <SectionCard
      step={4}
      title="기준별 반영 비율"
      description="추천 점수를 낼 때 각 기준을 얼마나 반영할지 정합니다 · 합계 100% · 0%인 기준은 계산하지 않습니다"
    >
      <ul className="divide-y divide-border rounded-control border border-border">
        {CRITERIA.map((c) => {
          const Icon = ICON[c.key]
          const disabled = DISABLED_CRITERIA.includes(c.key)
          const pending = DATA_PENDING_CRITERIA.includes(c.key)
          const value = weights[c.key]
          return (
            <li
              key={c.key}
              className={cx('flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3', disabled && 'opacity-50')}
            >
              <span className="flex size-8 shrink-0 items-center justify-center rounded-control bg-blue-50 text-blue">
                <Icon className="size-4" aria-hidden />
              </span>
              <div className="w-52 min-w-0">
                <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium text-text">
                  {c.label}
                  {pending && <Badge tone="amber">데이터 준비 중</Badge>}
                  {disabled && <Badge>추후 제공</Badge>}
                </p>
                <p className="text-xs text-muted">{c.description}</p>
              </div>
              <input
                type="range"
                min={0}
                max={WEIGHTS_TOTAL}
                value={value}
                disabled={disabled}
                aria-label={`${c.label} 반영 비율`}
                onChange={(e) => set(c.key, Number(e.target.value))}
                className="h-2 min-w-32 flex-1 cursor-pointer accent-blue disabled:cursor-not-allowed"
              />
              <div className="flex h-10 w-20 items-center rounded-control border border-border bg-white px-3 focus-within:border-blue focus-within:ring-1 focus-within:ring-blue">
                <input
                  type="number"
                  inputMode="numeric"
                  min={0}
                  max={WEIGHTS_TOTAL}
                  value={value}
                  disabled={disabled}
                  aria-label={`${c.label} 반영 비율 (%)`}
                  onChange={(e) => set(c.key, Number(e.target.value))}
                  className="w-full min-w-0 bg-transparent text-right text-sm font-bold text-text outline-none"
                />
                <span className="ml-1 text-sm text-muted">%</span>
              </div>
            </li>
          )
        })}
      </ul>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p role="status" className="flex items-center gap-2 text-xs text-muted">
          {ok ? (
            <Badge tone="green">
              <CircleCheck className="size-3" aria-hidden />
              합계 100%
            </Badge>
          ) : (
            <Badge tone="red">
              <CircleAlert className="size-3" aria-hidden />
              합계 {sum}%
            </Badge>
          )}
          100%가 아니면 저장·추천 요청을 할 수 없습니다
        </p>
        <Button variant="secondary" size="sm" onClick={() => onChange(EQUAL_WEIGHTS)}>
          균등하게 나누기 (5개 기준 20%씩)
        </Button>
      </div>

      <p className="mt-4 flex items-start gap-2 rounded-control bg-blue-50 px-4 py-3 text-sm text-blue">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        데이터가 없어 계산하지 못한 기준의 비율은 나머지 기준에 비율대로 나눠 적용됩니다. 실제 적용 비율은 추천
        결과에서 확인할 수 있습니다.
      </p>
    </SectionCard>
  )
}
