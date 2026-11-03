import type { ButtonHTMLAttributes } from 'react'
import { cx } from '../../utils/cx'

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean
}

// 토글형 선택 칩. 선택 상태는 aria-pressed로 노출한다.
export function Chip({ selected = false, className, type = 'button', ...rest }: ChipProps) {
  return (
    <button
      type={type}
      aria-pressed={selected}
      className={cx(
        'inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm transition-colors',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue',
        selected
          ? 'border-blue bg-blue-50 font-medium text-blue'
          : 'border-border bg-white text-text hover:bg-bg',
        className,
      )}
      {...rest}
    />
  )
}
