import { Heart } from 'lucide-react'
import { useFavorites } from '../../contexts/FavoriteContext'
import { cx } from '../../utils/cx'

interface FavoriteButtonProps {
  propertyId: string
  withLabel?: boolean // true 면 '찜하기 / 찜함' 글자를 함께 보여 준다
  className?: string
}

// 매물 찜 하트 버튼. 카드 클릭(선택)으로 번지지 않게 stopPropagation 한다
export function FavoriteButton({ propertyId, withLabel = false, className }: FavoriteButtonProps) {
  const fav = useFavorites()
  const on = fav.has(propertyId)
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={withLabel ? undefined : on ? '찜 해제' : '찜하기'}
      title={on ? '찜 해제' : '찜하기'}
      onClick={(e) => {
        e.stopPropagation()
        fav.toggle(propertyId)
      }}
      className={cx(
        'inline-flex shrink-0 items-center justify-center gap-1 rounded-control text-xs font-medium hover:bg-bg focus-visible:outline-2 focus-visible:outline-blue',
        withLabel ? 'h-8 border border-border bg-white px-3' : 'size-8',
        on ? 'text-red' : 'text-muted',
        className,
      )}
    >
      <Heart className={cx('size-4', on && 'fill-current')} aria-hidden />
      {withLabel && (on ? '찜함' : '찜하기')}
    </button>
  )
}
