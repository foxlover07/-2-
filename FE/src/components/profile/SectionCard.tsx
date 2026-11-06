import type { ReactNode } from 'react'
import { Card } from '../common'

interface SectionCardProps {
  step: number
  title: ReactNode
  description: ReactNode
  children: ReactNode
}

// 생활 프로필의 번호 매긴 섹션 (Figma 03 v2).
export function SectionCard({ step, title, description, children }: SectionCardProps) {
  return (
    <Card padding="lg">
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-navy text-sm font-bold text-white"
          aria-hidden
        >
          {step}
        </span>
        <div className="min-w-0">
          <h2 className="text-lg font-bold text-navy">{title}</h2>
          <p className="mt-0.5 text-sm text-muted">{description}</p>
        </div>
      </div>
      <div className="mt-5">{children}</div>
    </Card>
  )
}
