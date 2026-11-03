import type { ReactNode } from 'react'
import { Inbox, type LucideIcon } from 'lucide-react'
import { cx } from '../../utils/cx'

interface EmptyStateProps {
  title: ReactNode
  description?: ReactNode
  icon?: LucideIcon
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, description, icon: Icon = Inbox, action, className }: EmptyStateProps) {
  return (
    <div className={cx('flex flex-col items-center justify-center px-6 py-12 text-center', className)}>
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-blue-50 text-blue">
        <Icon className="size-6" aria-hidden />
      </div>
      <p className="text-base font-bold text-text">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-sm text-muted">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}
