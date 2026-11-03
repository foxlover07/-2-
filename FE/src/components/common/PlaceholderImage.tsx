import { House } from 'lucide-react'
import { cx } from '../../utils/cx'

interface PlaceholderImageProps {
  className?: string
  label?: string
}

// 매물 사진이 없으므로 항상 이 Placeholder를 사용한다.
export function PlaceholderImage({ className, label = '매물 사진 없음' }: PlaceholderImageProps) {
  return (
    <div
      role="img"
      aria-label={label}
      className={cx('flex items-center justify-center bg-blue-50 text-blue/40', className)}
    >
      <House className="size-1/3 max-h-12 max-w-12" strokeWidth={1.5} aria-hidden />
    </div>
  )
}
