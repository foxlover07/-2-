import {
  DEPOSIT_BUDGET_SLACK_RATIO,
  LARGER_AREA_OPTIONS,
  MONTHLY_BUDGET_SLACK_MANWON,
  type LargerArea,
} from '../../constants/recommend'
import { Chip } from '../common'
import { SectionCard } from './SectionCard'

// 유지비 슬라이더 범위 (Figma 03 v2). 입력칸에는 이 범위 밖 값도 넣을 수 있다.
const BUDGET_SLIDER_MIN = 30
const BUDGET_SLIDER_MAX = 150

type TextField = 'budgetText' | 'depositText' | 'commuteText' | 'areaText'

interface ConditionsSectionProps {
  step: number
  budgetText: string
  depositText: string
  commuteText: string
  areaText: string
  largerArea: LargerArea
  budget: number | null | undefined
  errors: { budget: string | null; deposit: string | null; commute: string | null; area: string | null }
  onChange: (patch: Partial<Record<TextField, string>> & { largerArea?: LargerArea }) => void
}

function NumberInput({
  label,
  value,
  invalid,
  placeholder,
  unit,
  onChange,
}: {
  label: string
  value: string
  invalid: boolean
  placeholder: string
  unit: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex h-12 w-48 items-center rounded-control border border-border bg-white px-3 focus-within:border-blue focus-within:ring-1 focus-within:ring-blue">
      <input
        aria-label={label}
        type="number"
        inputMode="decimal"
        min={0}
        placeholder={placeholder}
        value={value}
        aria-invalid={invalid}
        onChange={(e) => onChange(e.target.value)}
        className="w-full min-w-0 bg-transparent text-base font-bold text-text outline-none placeholder:text-sm placeholder:font-normal placeholder:text-muted"
      />
      <span className="ml-2 shrink-0 text-sm text-muted">{unit}</span>
    </div>
  )
}

function Help({ error, children }: { error: string | null; children: string }) {
  return <p className={error ? 'mt-2 text-xs text-red' : 'mt-2 text-xs text-muted'}>{error ?? children}</p>
}

// 조건 입력 (통합안 1.1): 유지비 상한, 보증금 상한, 희망 통학 시간, 희망 면적, 더 넓은 면적 선호.
// 입력한 값이 각 요소의 '기준'(50점)이 된다.
export function ConditionsSection({
  step,
  budgetText,
  depositText,
  commuteText,
  areaText,
  largerArea,
  budget,
  errors,
  onChange,
}: ConditionsSectionProps) {
  const sliderBudget = Math.min(BUDGET_SLIDER_MAX, Math.max(BUDGET_SLIDER_MIN, budget ?? BUDGET_SLIDER_MIN))

  return (
    <SectionCard step={step} title="조건 입력" description="입력한 값이 기준이 됩니다 · 기준보다 좋으면 점수가 오르고, 나쁘면 두 배로 빠르게 내려갑니다">
      <p className="mb-2 text-sm font-medium text-text">유지비 상한 (월세 + 관리비, 필수)</p>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <NumberInput
          label="유지비 상한"
          value={budgetText}
          invalid={errors.budget !== null}
          placeholder="미설정"
          unit="만원 / 월"
          onChange={(budgetText) => onChange({ budgetText })}
        />
        <div className="min-w-48 flex-1">
          <input
            type="range"
            aria-label="유지비 상한 슬라이더"
            min={BUDGET_SLIDER_MIN}
            max={BUDGET_SLIDER_MAX}
            value={sliderBudget}
            onChange={(e) => onChange({ budgetText: e.target.value })}
            className="h-2 w-full cursor-pointer accent-blue"
          />
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{BUDGET_SLIDER_MIN}만원</span>
            <span>{BUDGET_SLIDER_MAX}만원</span>
          </div>
        </div>
      </div>
      <Help error={errors.budget}>
        {`상한 + ${MONTHLY_BUDGET_SLACK_MANWON}만 원을 넘는 매물은 제외하고, 상한과 그 사이는 "예산 초과"로 표시합니다. 관리비 정보가 없는 매물은 지금은 제외됩니다.`}
      </Help>

      <p className="mt-6 mb-2 text-sm font-medium text-text">보증금 상한 (선택)</p>
      <NumberInput
        label="보증금 상한"
        value={depositText}
        invalid={errors.deposit !== null}
        placeholder="미설정"
        unit="만원"
        onChange={(depositText) => onChange({ depositText })}
      />
      <Help error={errors.deposit}>
        {`상한의 ${Math.round((1 + DEPOSIT_BUDGET_SLACK_RATIO) * 100)}%를 넘는 매물은 제외합니다. 비워 두면 보증금은 보지 않습니다.`}
      </Help>

      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <div>
          <p className="mb-2 text-sm font-medium text-text">희망 통학 시간 (편도, 필수)</p>
          <NumberInput
            label="희망 통학 시간"
            value={commuteText}
            invalid={errors.commute !== null}
            placeholder="예: 15"
            unit="분 · 도보"
            onChange={(commuteText) => onChange({ commuteText })}
          />
          <Help error={errors.commute}>도보 기준입니다. 학과 건물·시간표 건물까지 편도 시간과 비교합니다.</Help>
        </div>
        <div>
          <p className="mb-2 text-sm font-medium text-text">희망 면적 (필수)</p>
          <NumberInput
            label="희망 면적"
            value={areaText}
            invalid={errors.area !== null}
            placeholder="예: 20"
            unit="㎡"
            onChange={(areaText) => onChange({ areaText })}
          />
          <Help error={errors.area}>희망보다 좁으면 2㎡마다 점수가 내려갑니다.</Help>
        </div>
      </div>

      <fieldset className="mt-6">
        <legend className="mb-2 text-sm font-medium text-text">희망보다 더 넓은 면적</legend>
        <div className="flex flex-wrap gap-2">
          {LARGER_AREA_OPTIONS.map((o) => (
            <Chip key={o.value} selected={largerArea === o.value} onClick={() => onChange({ largerArea: o.value })}>
              {o.label}
            </Chip>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">{LARGER_AREA_OPTIONS.find((o) => o.value === largerArea)?.help}</p>
      </fieldset>
    </SectionCard>
  )
}
