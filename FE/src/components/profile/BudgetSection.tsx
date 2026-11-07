import { DEPOSIT_BUDGET_SLACK_PERCENT, MONTHLY_BUDGET_SLACK_MANWON } from '../../constants/recommend'
import { SectionCard } from './SectionCard'

// 예산 슬라이더 범위 (Figma 03 v2). 입력칸에는 이 범위 밖 값도 넣을 수 있다.
const BUDGET_SLIDER_MIN = 30
const BUDGET_SLIDER_MAX = 150

interface BudgetSectionProps {
  step: number
  budgetText: string
  depositText: string
  budget: number | null | undefined
  budgetError: string | null
  depositError: string | null
  onChange: (patch: { budgetText?: string; depositText?: string }) => void
}

function ManwonInput({
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

export function BudgetSection({
  step,
  budgetText,
  depositText,
  budget,
  budgetError,
  depositError,
  onChange,
}: BudgetSectionProps) {
  const sliderBudget = Math.min(BUDGET_SLIDER_MAX, Math.max(BUDGET_SLIDER_MIN, budget ?? BUDGET_SLIDER_MIN))

  return (
    <SectionCard step={step} title="예산" description="월 고정 주거비(월세 + 관리비) 기준 · 추천에는 월 예산이 필요합니다">
      <p className="mb-2 text-sm font-medium text-text">월 주거비 예산 (필수)</p>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <ManwonInput
          label="월 주거비 예산"
          value={budgetText}
          invalid={budgetError !== null}
          placeholder="예산 미설정"
          unit="만원 / 월"
          onChange={(budgetText) => onChange({ budgetText })}
        />
        <div className="min-w-48 flex-1">
          <input
            type="range"
            aria-label="월 주거비 예산 슬라이더"
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
      <p className={budgetError ? 'mt-2 text-xs text-red' : 'mt-2 text-xs text-muted'}>
        {budgetError ??
          `예산 + ${MONTHLY_BUDGET_SLACK_MANWON}만원까지는 '예산 초과'로 표시하고(주거비 점수 0점), 그보다 비싸면 추천에서 제외합니다. 관리비 정보가 없는 매물은 지금은 제외됩니다.`}
      </p>

      <p className="mt-6 mb-2 text-sm font-medium text-text">보증금 예산 (선택)</p>
      <ManwonInput
        label="보증금 예산"
        value={depositText}
        invalid={depositError !== null}
        placeholder="미설정"
        unit="만원"
        onChange={(depositText) => onChange({ depositText })}
      />
      <p className={depositError ? 'mt-2 text-xs text-red' : 'mt-2 text-xs text-muted'}>
        {depositError ??
          `보증금이 예산의 ${100 + DEPOSIT_BUDGET_SLACK_PERCENT}%를 넘으면 제외합니다. 비워 두면 보증금은 보지 않습니다.`}
      </p>
    </SectionCard>
  )
}
