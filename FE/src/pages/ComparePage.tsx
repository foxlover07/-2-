import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Columns3, Plus, TriangleAlert } from 'lucide-react'
import { Button, Card, EmptyState, Loading } from '../components/common'
import { CompareTable } from '../components/compare/CompareTable'
import { RecommendPanel } from '../components/compare/RecommendPanel'
import { PageContainer } from '../components/layout/PageContainer'
import { MAX_COMPARE, useCompare } from '../contexts/CompareContext'
import { useProfile } from '../contexts/ProfileContext'
import { getProperties } from '../services/propertyService'
import type { Property } from '../types/property'
import { findCheapest, getMonthlyFixedCost } from '../utils/cost'
import { formatManwon, formatMinutes } from '../utils/format'
import { departmentVisits } from '../utils/profile'
import { findShortestWalk, getWeightedAvgWalk } from '../utils/walk'

const MIN_COMPARE = 2

const LINK_BUTTON =
  'inline-flex h-10 items-center gap-1.5 rounded-control bg-blue px-4 text-sm font-medium text-white hover:bg-blue/90'

export function ComparePage() {
  const compare = useCompare()
  const { profile, isSaved } = useProfile()
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

  // 비교 목록 순서를 유지한다. 데이터에 없는 ID는 제외한다.
  const selected = useMemo(
    () => compare.ids.flatMap((id) => all?.find((p) => p.property_id === id) ?? []),
    [all, compare.ids],
  )
  const missingCount = all ? compare.ids.length - selected.length : 0

  const cheapest = findCheapest(selected)
  // 학과 건물 평균 도보시간 (강의동 방문 횟수 입력은 통합안에서 없앴다)
  const visits = departmentVisits(profile)
  const shortest = findShortestWalk(selected, visits)

  if (loadError) {
    return (
      <PageContainer title="매물 비교">
        <Card padding="none">
          <EmptyState icon={TriangleAlert} title="매물 정보를 불러오지 못했습니다" description="잠시 후 다시 시도해 주세요." />
        </Card>
      </PageContainer>
    )
  }
  if (all === null) {
    return (
      <PageContainer title="매물 비교">
        <Loading message="매물 정보를 불러오는 중입니다…" className="py-12" />
      </PageContainer>
    )
  }

  return (
    <PageContainer
      title="매물 비교"
      description={`조건 기반 추천을 확인하고, 최대 ${MAX_COMPARE}개 매물의 비용과 강의동까지 도보시간을 나란히 비교합니다.`}
    >
      <div className="mb-5">
        <RecommendPanel profile={profile} isSaved={isSaved} properties={all} />
      </div>

      {missingCount > 0 && (
        <p className="mb-4 rounded-control bg-bg px-4 py-2 text-sm text-muted">
          비교 목록 중 {missingCount}개 매물은 데이터에서 찾을 수 없어 제외했습니다.
        </p>
      )}

      {selected.length < MIN_COMPARE ? (
        <Card padding="none">
          <EmptyState
            icon={Columns3}
            title={`비교하려면 매물을 ${MIN_COMPARE}개 이상 담아 주세요`}
            description={
              selected.length === 0
                ? '매물 탐색에서 카드의 "비교"를 체크하면 이곳에서 비교할 수 있어요.'
                : `지금 ${selected[0].property_id} 1개가 담겨 있습니다. 1개 이상 더 담아 주세요.`
            }
            action={
              <Link to="/" className={LINK_BUTTON}>
                <Plus className="size-4" aria-hidden />
                매물 담으러 가기
              </Link>
            }
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-5">
          <div className="grid gap-3 md:grid-cols-2">
            <SummaryCard
              tone="green"
              title="최저 비용 매물"
              property={cheapest}
              value={cheapest && `월 고정비 ${formatManwon(getMonthlyFixedCost(cheapest))}`}
              emptyText="비용 정보가 있는 매물이 없습니다."
            />
            <SummaryCard
              tone="blue"
              title="최단 이동 매물"
              property={shortest}
              value={shortest && `학과 건물 평균 ${formatMinutes(getWeightedAvgWalk(shortest, visits))}`}
              emptyText="생활 프로필에 학과 건물을 입력하면 알려 드려요."
            />
          </div>

          <Card padding="none" className="overflow-hidden">
            <CompareTable
              properties={selected}
              cheapestId={cheapest?.property_id ?? null}
              shortestId={shortest?.property_id ?? null}
              visits={visits}
              budget={profile.budgetManwon}
              onRemove={compare.remove}
            />
          </Card>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted">
              비교 중 {selected.length}/{MAX_COMPARE}
            </p>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={compare.clear}>
                비교 목록 비우기
              </Button>
              {!compare.isFull && (
                <Link
                  to="/"
                  className="inline-flex h-8 items-center gap-1 rounded-control border border-border bg-white px-3 text-sm font-medium text-text hover:bg-bg"
                >
                  <Plus className="size-3.5" aria-hidden />
                  매물 더 담기
                </Link>
              )}
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  )
}

interface SummaryCardProps {
  tone: 'green' | 'blue'
  title: string
  property: Property | null
  value: string | null
  emptyText: string
}

function SummaryCard({ tone, title, property, value, emptyText }: SummaryCardProps) {
  return (
    <div className={tone === 'green' ? 'rounded-card bg-green-50 p-4' : 'rounded-card bg-blue-50 p-4'}>
      <p className={tone === 'green' ? 'text-xs font-bold text-green' : 'text-xs font-bold text-blue'}>{title}</p>
      {property ? (
        <>
          <p className="mt-1 text-base font-bold break-keep text-text">
            {property.property_id} · {property.address}
          </p>
          <p className="mt-0.5 text-sm font-medium text-text">{value}</p>
        </>
      ) : (
        <p className="mt-1 text-sm text-muted">{emptyText}</p>
      )}
    </div>
  )
}
