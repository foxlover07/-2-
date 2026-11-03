import type { HTMLAttributes, Ref } from 'react'
import { cx } from '../../utils/cx'

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: 'none' | 'sm' | 'md' | 'lg'
  selected?: boolean
  ref?: Ref<HTMLDivElement>
}

const PADDING = { none: '', sm: 'p-3', md: 'p-5', lg: 'p-6 md:p-8' }

export function Card({ padding = 'md', selected = false, className, ...rest }: CardProps) {
  return (
    <div
      className={cx(
        'rounded-card border bg-white',
        selected ? 'border-blue ring-1 ring-blue' : 'border-border',
        PADDING[padding],
        className,
      )}
      {...rest}
    />
  )
}
