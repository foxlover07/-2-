import type { HTMLAttributes } from 'react'
import { cx } from '../../utils/cx'

type Tone = 'blue' | 'green' | 'navy' | 'gray'

const TONE: Record<Tone, string> = {
  blue: 'bg-blue-50 text-blue',
  green: 'bg-green-50 text-green',
  navy: 'bg-navy text-white',
  gray: 'bg-bg text-muted',
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
}

export function Badge({ tone = 'gray', className, ...rest }: BadgeProps) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap',
        TONE[tone],
        className,
      )}
      {...rest}
    />
  )
}
