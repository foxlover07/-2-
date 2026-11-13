import { ChevronRight, Info } from 'lucide-react'
import { FACTORS, RANK_WEIGHTS, factorLabel, type Factor } from '../../constants/recommend'
import { cx } from '../../utils/cx'
import { SectionCard } from './SectionCard'

interface PrioritySectionProps {
  step: number
  selected: Factor[] // 1·2순위 (누른 순서)
  onChange: (next: Factor[]) => void
}

const pct = (w: number) => `${Math.round(w * 100)}%`

// 예산 / 이동 시간 / 면적 중 1·2순위를 누른 순서대로 고른다. 3순위는 남은 하나로 자동. 다시 누르면 빠진다.
export function PrioritySection({ step, selected, onChange }: PrioritySectionProps) {
  const toggle = (key: Factor) => {
    if (selected.includes(key)) onChange(selected.filter((k) => k !== key))
    else if (selected.length < 2) onChange([...selected, key])
  }
  const third = selected.length === 2 ? FACTORS.find((f) => !selected.includes(f.key))?.key : undefined
  const order: Factor[] = third ? [...selected, third] : selected

  return (
    <SectionCard
      step={step}
      title="우선순위"
      description={`1순위와 2순위를 차례로 고르면 3순위는 자동으로 정해집니다 · 가중치 ${RANK_WEIGHTS.map(pct).join(' / ')}`}
    >
      <ul className="grid gap-3 sm:grid-cols-3">
        {FACTORS.map((f) => {
          const rank = order.indexOf(f.key) + 1
          const picked = selected.includes(f.key)
          const auto = f.key === third
          const disabled = !picked && selected.length >= 2
          return (
            <li key={f.key}>
              <button
                type="button"
                aria-pressed={picked}
                disabled={disabled && !auto}
                onClick={() => toggle(f.key)}
                className={cx(
                  'flex h-full w-full flex-col gap-1.5 rounded-control border p-3.5 text-left transition-colors',
                  picked ? 'border-2 border-blue bg-blue-50' : auto ? 'border-dashed border-blue/50 bg-white' : 'border-border bg-white hover:bg-bg',
                  disabled && !auto && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className={cx(
                      'flex size-5.5 shrink-0 items-center justify-center rounded-full text-xs font-bold',
                      picked ? 'bg-navy text-white' : auto ? 'bg-blue-50 text-blue' : 'border border-border text-muted',
                    )}
                    aria-hidden
                  >
                    {rank > 0 ? rank : ''}
                  </span>
                  <span className="text-[15px] font-bold text-navy">{f.label}</span>
                  {rank > 0 && <span className="ml-auto text-xs text-muted">{pct(RANK_WEIGHTS[rank - 1])}{auto && ' · 자동'}</span>}
                </span>
                <span className="text-xs text-muted">{f.description}</span>
              </button>
            </li>
          )
        })}
      </ul>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-bold text-navy">순서:</span>
        {selected.length === 0 ? (
          <span className="text-muted">1순위부터 골라 주세요.</span>
        ) : (
          order.map((k, i) => (
            <span key={k} className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 rounded-full border border-border bg-white py-1 pr-2.5 pl-1">
                <span className="flex size-5 items-center justify-center rounded-full bg-navy text-xs font-bold text-white">{i + 1}</span>
                {factorLabel(k)}
              </span>
              {i < order.length - 1 && <ChevronRight className="size-3.5 text-muted" aria-hidden />}
            </span>
          ))
        )}
        {selected.length === 1 && <span className="text-muted">· 2순위를 골라 주세요.</span>}
      </div>

      <p className="mt-4 flex items-start gap-2 rounded-control bg-blue-50 px-4 py-3 text-sm text-blue">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        세 요소는 같은 기준으로 점수를 매깁니다. 편도 10분 = 월 5만 원 = 면적 2㎡ 차이가 같은 무게입니다.
      </p>
    </SectionCard>
  )
}
