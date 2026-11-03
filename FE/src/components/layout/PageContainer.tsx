import type { ReactNode } from 'react'
import { cx } from '../../utils/cx'

interface PageContainerProps {
  title?: ReactNode
  description?: ReactNode
  children: ReactNode
  narrow?: boolean
  className?: string
}

// 지도 화면을 제외한 일반 페이지의 공통 폭·여백.
export function PageContainer({ title, description, children, narrow = false, className }: PageContainerProps) {
  return (
    <div
      className={cx('mx-auto w-full px-4 py-6 md:px-6 md:py-10', narrow ? 'max-w-3xl' : 'max-w-5xl', className)}
    >
      {title && (
        <div className="mb-6">
          <h1 className="text-xl font-bold text-navy md:text-2xl">{title}</h1>
          {description && <p className="mt-1.5 text-sm text-muted">{description}</p>}
        </div>
      )}
      {children}
    </div>
  )
}
