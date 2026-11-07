import { Info } from 'lucide-react'
import { DATA_PENDING_FACTORS, FACTORS, RANK_WEIGHTS, type Priorities } from '../../constants/recommend'
import { Badge } from '../common'
import { SectionCard } from './SectionCard'
import { PriorityPicker } from './PriorityPicker'

interface PrioritySectionProps {
  step: number
  priorities: Priorities
  onChange: (priorities: Priorities) => void
}

const pct = (w: number) => `${Math.round(w * 100)}%`

export function PrioritySection({ step, priorities, onChange }: PrioritySectionProps) {
  return (
    <SectionCard
      step={step}
      title="우선순위"
      description={`가장 중요한 것 2개만 고르세요 · 1순위 ${pct(RANK_WEIGHTS[0])} · 2순위 ${pct(RANK_WEIGHTS[1])} · 3순위(자동) ${pct(RANK_WEIGHTS[2])}`}
    >
      <PriorityPicker priorities={priorities} onChange={onChange} />
      <ul className="mt-4 grid gap-2 text-xs text-muted sm:grid-cols-3">
        {FACTORS.map((f) => (
          <li key={f.key} className="flex flex-col items-start gap-0.5">
            <span className="flex items-center gap-1.5 font-medium text-text">
              {f.label}
              {DATA_PENDING_FACTORS.includes(f.key) && <Badge tone="amber">데이터 준비 중</Badge>}
            </span>
            {f.description}
          </li>
        ))}
      </ul>
      <p className="mt-4 flex items-start gap-2 rounded-control bg-blue-50 px-4 py-3 text-sm text-blue">
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
        아직 계산할 수 없는 요소(교통 편의성)의 비중은 나머지 요소에 비율대로 나눠 적용됩니다. 추천 결과 화면에서도
        순위를 바꿔 볼 수 있습니다.
      </p>
    </SectionCard>
  )
}
