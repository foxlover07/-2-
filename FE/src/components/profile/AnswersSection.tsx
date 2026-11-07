import { ANSWER_FIELDS, categoryInfo, type Answers, type CategoryKey } from '../../constants/recommend'
import { Badge } from '../common'
import { SectionCard } from './SectionCard'
import { Stepper } from './Stepper'

interface AnswersSectionProps {
  step: number
  categories: CategoryKey[]
  answers: Answers
  hasTimetable: boolean
  onChange: (answers: Answers) => void
}

const hasQuestion = (k: CategoryKey): k is keyof typeof ANSWER_FIELDS => k in ANSWER_FIELDS

// 고른 카테고리의 질문만 보여 준다. 준비 중 카테고리는 안내만 한다.
export function AnswersSection({ step, categories, answers, hasTimetable, onChange }: AnswersSectionProps) {
  return (
    <SectionCard step={step} title="고른 기준에 답하기" description="고른 기준에 대한 질문만 나옵니다 · 예시 답안을 참고하세요">
      {categories.length === 0 ? (
        <p className="text-sm text-muted">위에서 기준을 1개 이상 고르면 질문이 나타납니다.</p>
      ) : (
        <ol className="flex flex-col gap-3">
          {categories.map((k, i) => {
            const info = categoryInfo(k)
            return (
              <li key={k} className="rounded-control border border-border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="flex size-6 items-center justify-center rounded-full bg-navy text-xs font-bold text-white">
                    {i + 1}
                  </span>
                  <span className="text-[15px] font-bold text-navy">{info?.label}</span>
                  <Badge tone="blue">{i + 1}순위</Badge>
                  {!info?.ready && <Badge tone="amber">데이터 준비 중</Badge>}
                </div>
                {hasQuestion(k) ? (
                  <QuestionBody
                    spec={ANSWER_FIELDS[k]}
                    value={answers[ANSWER_FIELDS[k].field]}
                    needsTimetable={k !== 'weekly_commute' && !hasTimetable}
                    onChange={(v) => onChange({ ...answers, [ANSWER_FIELDS[k].field]: v })}
                  />
                ) : (
                  <p className="mt-3 text-sm text-muted">
                    이 기준은 데이터가 준비되면 질문이 열립니다. 지금은 골라도 점수에 반영되지 않고, 비중이 나머지 기준에 나눠집니다.
                  </p>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </SectionCard>
  )
}

function QuestionBody({
  spec,
  value,
  needsTimetable,
  onChange,
}: {
  spec: (typeof ANSWER_FIELDS)[keyof typeof ANSWER_FIELDS]
  value: number
  needsTimetable: boolean
  onChange: (v: number) => void
}) {
  return (
    <div className="mt-3 flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-text">{spec.question}</p>
          <p className="text-xs text-muted">{spec.help}</p>
        </div>
        <Stepper
          label={spec.question}
          value={value}
          min={spec.min}
          max={spec.max}
          step={spec.step}
          unit={spec.unit}
          onChange={onChange}
        />
      </div>
      {needsTimetable && (
        <p className="rounded-control bg-amber-50 px-3 py-2 text-xs text-amber">
          아래 시간표를 입력해야 계산됩니다. 시간표가 없으면 이 기준의 비중은 나머지 기준에 나눠집니다.
        </p>
      )}
      <p className="flex items-start gap-2 rounded-control bg-bg px-3 py-2 text-xs text-text">
        <Badge tone="blue">예시 답안</Badge>
        <span className="pt-0.5">{spec.example}</span>
      </p>
    </div>
  )
}
