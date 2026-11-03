import type { Ref } from 'react'
import { Link } from 'react-router'
import { ChevronRight, MapPinOff } from 'lucide-react'
import { Badge, Card, PlaceholderImage } from '../common'
import type { Property } from '../../types/property'
import { getMonthlyFixedCost } from '../../utils/cost'
import { formatArea, formatManwon } from '../../utils/format'
import { cx } from '../../utils/cx'

interface PropertyCardProps {
  property: Property
  selected: boolean
  locationFailed: boolean
  compareChecked: boolean
  compareDisabled: boolean
  onSelect: () => void
  onToggleCompare: () => void
  ref?: Ref<HTMLDivElement>
}

export function PropertyCard({
  property: p,
  selected,
  locationFailed,
  compareChecked,
  compareDisabled,
  onSelect,
  onToggleCompare,
  ref,
}: PropertyCardProps) {
  return (
    <Card
      ref={ref}
      padding="none"
      selected={selected}
      onClick={onSelect}
      className="cursor-pointer p-3 transition-shadow hover:shadow-md"
    >
      <div className="flex gap-3">
        <PlaceholderImage className="size-20 shrink-0 rounded-control" />

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1">
              <Badge tone="blue">{p.property_type}</Badge>
              {locationFailed && (
                <Badge tone="gray">
                  <MapPinOff className="size-3" aria-hidden />
                  위치 확인 불가
                </Badge>
              )}
            </div>
            <label
              className={cx(
                'flex shrink-0 items-center gap-1 text-xs',
                compareDisabled ? 'cursor-not-allowed text-border' : 'cursor-pointer text-muted',
              )}
              onClick={(e) => e.stopPropagation()}
              title={compareDisabled ? '비교는 최대 3개까지 담을 수 있습니다.' : undefined}
            >
              <input
                type="checkbox"
                className="size-4 accent-blue"
                checked={compareChecked}
                disabled={compareDisabled}
                onChange={onToggleCompare}
              />
              비교
            </label>
          </div>

          <button
            type="button"
            className="mt-1 block text-left focus-visible:outline-2 focus-visible:outline-blue"
            aria-pressed={selected}
            onClick={(e) => {
              e.stopPropagation()
              onSelect()
            }}
          >
            <span className="text-xs text-muted">월 고정비 </span>
            <span className="text-lg font-bold text-blue">{formatManwon(getMonthlyFixedCost(p))}</span>
          </button>

          <p className="mt-0.5 flex flex-wrap gap-x-1.5 text-xs text-text">
            <span className="whitespace-nowrap">보증금 {formatManwon(p.deposit_manwon)}</span>
            <span className="whitespace-nowrap">· 월세 {formatManwon(p.monthly_rent_manwon)}</span>
            <span className="whitespace-nowrap">· 관리비 {formatManwon(p.maintenance_manwon)}</span>
          </p>
          <p className="mt-0.5 truncate text-xs text-muted">
            {formatArea(p.area_m2)} · {p.address}
          </p>
        </div>
      </div>

      <div className="mt-2 flex items-center justify-between border-t border-border pt-2">
        <span className="text-xs text-muted">{p.property_id}</span>
        <Link
          to={`/properties/${p.property_id}`}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center text-xs font-medium text-blue hover:underline"
        >
          상세 보기
          <ChevronRight className="size-3.5" aria-hidden />
        </Link>
      </div>
    </Card>
  )
}
