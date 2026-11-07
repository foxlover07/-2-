import { ChevronRight, Info } from 'lucide-react'
import { CATEGORIES, MAX_CATEGORIES, RANK_WEIGHTS, categoryInfo, type CategoryKey } from '../../constants/recommend'
import { cx } from '../../utils/cx'
import { Badge } from '../common'
import { SectionCard } from './SectionCard'

interface CategorySectionProps {
  step: number
  selected: CategoryKey[]
  onChange: (next: CategoryKey[]) => void
}

const pct = (w: number) => `${Math.round(w * 100)}%`

// 카테고리 9개 중 최대 3개를 누른 순서대로 고른다. 다시 누르면 빠지고 뒤 순위가 당겨진다.
export function CategorySection({ step, selected, onChange }: CategorySectionProps) {
  const toggle = (key: CategoryKey) => {
    if (selected.includes(key)) onChange(selected.filter((k) => k !== key))
    else if (selected.length < MAX_CATEGORIES) onChange([...selected, key])
  }
  const full = selected.length >= MAX_CATEGORIES
  const pickedPending = selected.filter((k) => !categoryInfo(k)?.ready)

  return (
    <SectionCard
      step={step}
      title={`중요한 것 ${MAX_CATEGORIES}가지 (순서대로)`}
      description={`누른 순서대로 1·2·3순위가 됩니다 · 가중치 ${RANK_WEIGHTS.map(pct).join(' / ')}는 자동으로 적용됩니다`}
    >
      <ul className="grid gap-3 sm:grid-cols-2 md:grid-cols-3">
        {CATEGORIES.map((c) => {
          const rank = selected.indexOf(c.key) + 1
          const on = rank > 0
          const disabled = !on && full
          return (
            <li key={c.key}>
              <button
                type="button"
                aria-pressed={on}
                disabled={disabled}
                onClick={() => toggle(c.key)}
                className={cx(
                  'flex h-full w-full flex-col gap-1.5 rounded-control border p-3.5 text-left transition-colors',
                  on ? 'border-2 border-blue bg-blue-50' : 'border-border bg-white hover:bg-bg',
                  disabled && 'cursor-not-allowed opacity-50',
                )}
              >
                <span className="flex items-center gap-1.5">
                  <span
                    className={cx(
                      'flex size-5.5 shrink-0 items-center justify-center rounded-full text-xs font-bold',
                      on ? 'bg-navy text-white' : 'border border-border text-muted',
                    )}
                    aria-hidden
                  >
                    {on ? rank : ''}
                  </span>
                  <span className="text-[11px] font-medium text-muted">
                    {c.no} · {c.group}
                  </span>
                  <span className="ml-auto">
                    {c.ready ? <Badge tone="green">사용 가능</Badge> : <Badge tone="amber">데이터 준비 중</Badge>}
                  </span>
                </span>
                <span className={cx('text-[15px] font-bold', c.ready ? 'text-navy' : 'text-muted')}>{c.label}</span>
                <span className="text-xs text-muted">{c.description}</span>
              </button>
            </li>
          )
        })}
      </ul>

      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-bold text-navy">선택:</span>
        {selected.length === 0 ? (
          <span className="text-muted">아직 고른 기준이 없습니다. 1개 이상 골라 주세요.</span>
        ) : (
          selected.map((k, i) => (
            <span key={k} className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 rounded-full border border-border bg-white py-1 pr-2.5 pl-1">
                <span className="flex size-5 items-center justify-center rounded-full bg-navy text-xs font-bold text-white">
                  {i + 1}
                </span>
                {categoryInfo(k)?.label}
              </span>
              {i < selected.length - 1 && <ChevronRight className="size-3.5 text-muted" aria-hidden />}
            </span>
          ))
        )}
      </div>

      <p className="mt-4 flex items-start gap-2 rounded-control bg-blue-50 px-4 py-3 text-sm text-blue">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        {pickedPending.length > 0
          ? `"${pickedPending.map((k) => categoryInfo(k)?.label).join('", "')}"은(는) 데이터 준비 중이라 점수를 만들지 않고, 그 비중을 나머지 기준에 나눠 적용합니다.`
          : '"데이터 준비 중" 기준을 고르면 그 비중은 나머지 기준에 나눠 적용되고, 결과 화면에 안내됩니다.'}
      </p>
    </SectionCard>
  )
}
