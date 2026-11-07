import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Building2, TriangleAlert } from 'lucide-react'
import { EmptyState } from '../components/common'
import { CompareBar } from '../components/explore/CompareBar'
import { FilterPanel } from '../components/explore/FilterPanel'
import { PropertyList } from '../components/explore/PropertyList'
import { MapView, type LocationStatus } from '../components/map/MapView'
import { getUniversityLabel } from '../constants/universities'
import { useCompare } from '../contexts/CompareContext'
import { useProfile } from '../contexts/ProfileContext'
import { useUniversity } from '../contexts/UniversityContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { cx } from '../utils/cx'
import { EMPTY_FILTERS, filterProperties, getFilterBounds, sortProperties, type SortKey } from '../utils/filter'

type LoadState = { kind: 'loading' } | { kind: 'ready'; data: Property[] } | { kind: 'error' }

export function ExplorePage() {
  const { university } = useUniversity()
  const compare = useCompare()
  const [load, setLoad] = useState<LoadState>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    getProperties()
      .then((data) => !cancelled && setLoad({ kind: 'ready', data }))
      .catch(() => !cancelled && setLoad({ kind: 'error' }))
    return () => {
      cancelled = true
    }
  }, [])

  const all = useMemo(() => (load.kind === 'ready' ? load.data : []), [load])
  const universityProperties = useMemo(() => all.filter((p) => p.university === university), [all, university])

  // 데스크톱에서는 화면 높이에 고정하고 필터·목록이 각자 스크롤한다. (헤더 64px + 테두리 1px)
  return (
    <div
      className={cx(
        'flex flex-1 flex-col lg:h-[calc(100svh-65px)] lg:flex-none lg:flex-row lg:overflow-hidden',
        compare.ids.length > 0 && 'pb-36 md:pb-20 lg:pb-0',
      )}
    >
      {/* 대학이 바뀌면 필터·선택 상태를 초기화한다. */}
      <ExploreView
        key={university}
        university={university}
        load={load}
        properties={universityProperties}
        listPaddingBottom={compare.ids.length > 0}
      />
      <CompareBar properties={all} />
    </div>
  )
}

interface ExploreViewProps {
  university: ReturnType<typeof useUniversity>['university']
  load: LoadState
  properties: Property[]
  listPaddingBottom: boolean
}

function ExploreView({ university, load, properties, listPaddingBottom }: ExploreViewProps) {
  // 생활 프로필의 유지비·보증금 상한을 기본값으로 쓴다 (초기화하면 조건 없음).
  const { profile } = useProfile()
  const [filters, setFilters] = useState(() => ({
    ...EMPTY_FILTERS,
    costMax: profile.budgetManwon,
    depositMax: profile.depositBudgetManwon,
  }))
  const [sortKey, setSortKey] = useState<SortKey>('cost')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [locationStatus, setLocationStatus] = useState<Record<string, LocationStatus>>({})

  const bounds = useMemo(() => getFilterBounds(properties), [properties])
  const visible = useMemo(
    () => sortProperties(filterProperties(properties, filters), sortKey),
    [properties, filters, sortKey],
  )
  const resetFilters = useCallback(() => setFilters(EMPTY_FILTERS), [])
  const mergeLocationStatus = useCallback(
    (next: Record<string, LocationStatus>) => setLocationStatus((prev) => ({ ...prev, ...next })),
    [],
  )

  let listContent: ReactNode
  if (load.kind === 'loading') {
    listContent = <p className="p-6 text-center text-sm text-muted">매물을 불러오는 중입니다…</p>
  } else if (load.kind === 'error') {
    listContent = <EmptyState icon={TriangleAlert} title="매물을 불러오지 못했습니다" description="잠시 후 다시 시도해 주세요." />
  } else if (properties.length === 0) {
    listContent = (
      <EmptyState
        icon={Building2}
        title={`${getUniversityLabel(university)} 매물 데이터가 아직 없습니다`}
        description="현재는 이화여대 매물만 제공합니다."
      />
    )
  } else {
    listContent = (
      <PropertyList
        properties={visible}
        selectedId={selectedId}
        locationStatus={locationStatus}
        sortKey={sortKey}
        onSortChange={setSortKey}
        onSelect={setSelectedId}
        onResetFilters={resetFilters}
      />
    )
  }

  return (
    <>
      <aside className="shrink-0 border-b border-border bg-white lg:w-72 lg:overflow-y-auto lg:border-r lg:border-b-0">
        <FilterPanel filters={filters} bounds={bounds} onChange={setFilters} onReset={resetFilters} />
      </aside>

      <section aria-label="지도" className="relative h-72 shrink-0 md:h-96 lg:h-auto lg:flex-1">
        <MapView
          properties={visible}
          selectedId={selectedId}
          onSelect={setSelectedId}
          university={university}
          onLocationStatusChange={mergeLocationStatus}
        />
      </section>

      <section
        aria-label="매물 목록"
        className={cx(
          'shrink-0 bg-bg lg:w-[400px] lg:overflow-y-auto lg:border-l lg:border-border',
          listPaddingBottom && 'lg:pb-20',
        )}
      >
        {listContent}
      </section>
    </>
  )
}
