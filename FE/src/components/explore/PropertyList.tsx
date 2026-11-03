import { useEffect, useRef } from 'react'
import { SearchX } from 'lucide-react'
import { Button, EmptyState } from '../common'
import { useCompare } from '../../contexts/CompareContext'
import type { Property } from '../../types/property'
import { SORT_OPTIONS, type SortKey } from '../../utils/filter'
import type { LocationStatus } from '../map/MapView'
import { PropertyCard } from './PropertyCard'

interface PropertyListProps {
  properties: Property[]
  selectedId: string | null
  locationStatus: Record<string, LocationStatus>
  sortKey: SortKey
  onSortChange: (key: SortKey) => void
  onSelect: (id: string) => void
  onResetFilters: () => void
}

export function PropertyList({
  properties,
  selectedId,
  locationStatus,
  sortKey,
  onSortChange,
  onSelect,
  onResetFilters,
}: PropertyListProps) {
  const compare = useCompare()
  const cardRefs = useRef(new Map<string, HTMLDivElement>())

  // 지도에서 선택하면 목록의 해당 카드가 보이도록 스크롤한다.
  useEffect(() => {
    if (selectedId) cardRefs.current.get(selectedId)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedId])

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-text">
          매물 <span className="font-bold text-blue">{properties.length}</span>개
        </p>
        <select
          aria-label="정렬"
          value={sortKey}
          onChange={(e) => onSortChange(e.target.value as SortKey)}
          className="h-9 rounded-control border border-border bg-white px-2 text-sm text-text focus:border-blue focus:outline-none"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {properties.length === 0 ? (
        <EmptyState
          icon={SearchX}
          title="조건에 맞는 매물이 없습니다"
          description="필터 조건을 넓혀 보세요."
          action={
            <Button variant="secondary" size="sm" onClick={onResetFilters}>
              필터 초기화
            </Button>
          }
        />
      ) : (
        properties.map((p) => (
          <PropertyCard
            key={p.property_id}
            ref={(el) => {
              if (el) cardRefs.current.set(p.property_id, el)
              else cardRefs.current.delete(p.property_id)
            }}
            property={p}
            selected={p.property_id === selectedId}
            locationFailed={locationStatus[p.property_id] === 'failed'}
            compareChecked={compare.has(p.property_id)}
            compareDisabled={!compare.has(p.property_id) && compare.isFull}
            onSelect={() => onSelect(p.property_id)}
            onToggleCompare={() => compare.toggle(p.property_id)}
          />
        ))
      )}
    </div>
  )
}
