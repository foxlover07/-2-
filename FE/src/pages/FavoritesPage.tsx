import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Check, ChevronRight, Heart, Plus, TriangleAlert } from 'lucide-react'
import { Badge, Card, EmptyState, FavoriteButton, Loading } from '../components/common'
import { PageContainer } from '../components/layout/PageContainer'
import { getUniversityLabel, isUniversityName } from '../constants/universities'
import { MAX_COMPARE, useCompare } from '../contexts/CompareContext'
import { useFavorites } from '../contexts/FavoriteContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { getMonthlyFixedCost } from '../utils/cost'
import { formatArea, formatManwon } from '../utils/format'

// 찜 목록 (2026-11-16): 하트를 누른 매물을 한곳에서 보고, 비교 목록(최대 3개)에 담아 비교로 넘어간다.
// 찜은 이 브라우저에만 저장된다.
export function FavoritesPage() {
  const favorites = useFavorites()
  const compare = useCompare()
  const [all, setAll] = useState<Property[] | null>(null)
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    let cancelled = false
    getProperties()
      .then((data) => !cancelled && setAll(data))
      .catch(() => !cancelled && setLoadError(true))
    return () => {
      cancelled = true
    }
  }, [])

  // 최근에 찜한 순서. 지금 데이터에 없는 매물(광고가 내려간 경우 등)은 따로 센다
  const items = useMemo(
    () => [...favorites.ids].reverse().flatMap((id) => all?.find((p) => p.property_id === id) ?? []),
    [all, favorites.ids],
  )
  const missing = all ? favorites.ids.length - items.length : 0

  const description = '하트를 누른 매물을 모아 봅니다. 이 브라우저에만 저장됩니다.'

  if (loadError) {
    return (
      <PageContainer title="찜 목록" description={description}>
        <Card padding="none">
          <EmptyState icon={TriangleAlert} title="매물 정보를 불러오지 못했습니다" description="잠시 후 다시 시도해 주세요." />
        </Card>
      </PageContainer>
    )
  }
  if (all === null) {
    return (
      <PageContainer title="찜 목록" description={description}>
        <Loading message="매물을 불러오는 중입니다…" />
      </PageContainer>
    )
  }

  return (
    <PageContainer title={`찜 목록 ${items.length}`} description={description}>
      {items.length === 0 ? (
        <Card padding="none">
          <EmptyState
            icon={Heart}
            title="아직 찜한 매물이 없습니다"
            description="매물 탐색·상세·추천 결과에서 하트를 누르면 여기에 모입니다."
            action={
              <Link to="/" className="text-sm font-medium text-blue hover:underline">
                매물 탐색으로 가기
              </Link>
            }
          />
        </Card>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
            <p className="text-muted">
              비교 목록에 담은 매물 {compare.ids.length}/{MAX_COMPARE}
              {compare.ids.length < 2 && ' · 2개 이상 담으면 비교할 수 있어요'}
            </p>
            {compare.ids.length >= 2 && (
              <Link
                to="/compare"
                className="inline-flex h-9 items-center gap-1 rounded-control bg-blue px-3.5 text-sm font-medium text-white hover:bg-blue/90"
              >
                담은 매물 비교하기
                <ChevronRight className="size-4" aria-hidden />
              </Link>
            )}
          </div>

          <ul className="flex flex-col gap-2.5">
            {items.map((p) => {
              const inCompare = compare.has(p.property_id)
              return (
                <li key={p.property_id}>
                  <Card padding="none" className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5">
                    <FavoriteButton propertyId={p.property_id} />
                    <div className="min-w-48 flex-1">
                      <p className="flex flex-wrap items-center gap-1.5">
                        <Link to={`/properties/${p.property_id}`} className="text-[15px] font-bold text-text hover:underline">
                          {p.address}
                        </Link>
                        <Badge tone="blue">{p.property_type}</Badge>
                        {isUniversityName(p.university) && <Badge tone="gray">{getUniversityLabel(p.university)}</Badge>}
                      </p>
                      <p className="mt-0.5 text-xs text-muted">
                        보증금 {formatManwon(p.deposit_manwon)} · 월세 {formatManwon(p.monthly_rent_manwon)} · 관리비{' '}
                        {formatManwon(p.maintenance_manwon)} · {formatArea(p.area_m2)}
                      </p>
                    </div>
                    <p className="text-right">
                      <span className="text-xs text-muted">월 고정비 </span>
                      <span className="text-lg font-bold text-blue">{formatManwon(getMonthlyFixedCost(p))}</span>
                    </p>
                    <button
                      type="button"
                      onClick={() => compare.toggle(p.property_id)}
                      disabled={!inCompare && compare.isFull}
                      title={!inCompare && compare.isFull ? `비교는 최대 ${MAX_COMPARE}개까지 담을 수 있습니다.` : undefined}
                      className="inline-flex h-8 items-center gap-1 rounded-control border border-border bg-white px-3 text-xs font-medium text-text hover:bg-bg disabled:cursor-not-allowed disabled:text-muted"
                    >
                      {inCompare ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}
                      {inCompare ? '비교 중' : '비교 담기'}
                    </button>
                    <Link
                      to={`/properties/${p.property_id}`}
                      className="inline-flex items-center text-xs font-medium text-blue hover:underline"
                    >
                      상세 보기
                      <ChevronRight className="size-3.5" aria-hidden />
                    </Link>
                  </Card>
                </li>
              )
            })}
          </ul>
          {missing > 0 && (
            <p className="mt-3 text-xs text-muted">
              찜한 매물 중 {missing}개는 지금 매물 데이터에 없습니다 (광고가 내려갔을 수 있음).
            </p>
          )}
        </>
      )}
    </PageContainer>
  )
}
