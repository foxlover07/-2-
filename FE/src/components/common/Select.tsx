import type { SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cx } from '../../utils/cx'

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
}

// 기본 select 에 Input 과 같은 모양만 입힌다.
export function Select({ invalid = false, className, children, ...rest }: SelectProps) {
  return (
    <div className={cx('relative', className)}>
      <select
        aria-invalid={invalid || undefined}
        className={cx(
          'h-10 w-full appearance-none rounded-control border bg-white pr-9 pl-3 text-sm text-text outline-none',
          'focus:border-blue focus:ring-1 focus:ring-blue disabled:bg-bg disabled:text-muted',
          invalid ? 'border-red' : 'border-border',
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
    </div>
  )
}
