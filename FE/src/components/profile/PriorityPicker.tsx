import { FACTORS, factorLabel, type Factor, type Priorities } from '../../constants/recommend'
import { Chip } from '../common'

interface PriorityPickerProps {
  priorities: Priorities
  onChange: (priorities: Priorities) => void
  compact?: boolean
}

// 1순위·2순위 고르기. 3순위는 남은 요소로 자동. 생활 프로필과 추천 결과 화면에서 같이 쓴다.
export function PriorityPicker({ priorities, onChange, compact = false }: PriorityPickerProps) {
  const [first, second] = priorities
  const third = FACTORS.find((f) => f.key !== first && f.key !== second)?.key

  const pick = (rank: 0 | 1, key: Factor) => {
    const other = priorities[rank === 0 ? 1 : 0]
    if (key === priorities[rank]) return
    // 다른 순위에 이미 있는 요소를 고르면 두 순위를 맞바꾼다
    const next: Priorities = rank === 0 ? [key, key === other ? first : other] : [key === other ? second : other, key]
    onChange(next)
  }

  const row = (rank: 0 | 1, label: string) => (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={label}>
      <span className={compact ? 'w-12 text-xs font-bold text-navy' : 'w-14 text-sm font-bold text-navy'}>{label}</span>
      {FACTORS.map((f) => (
        <Chip
          key={f.key}
          selected={priorities[rank] === f.key}
          onClick={() => pick(rank, f.key)}
          className={compact ? 'h-8 px-3' : undefined}
        >
          {f.label}
        </Chip>
      ))}
    </div>
  )

  return (
    <div className="flex flex-col gap-2">
      {row(0, '1순위')}
      {row(1, '2순위')}
      <p className={compact ? 'text-xs text-muted' : 'text-sm text-muted'}>
        3순위(자동): <span className="font-medium text-text">{third ? factorLabel(third) : '-'}</span>
      </p>
    </div>
  )
}
