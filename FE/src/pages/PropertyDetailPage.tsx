import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, Check, ExternalLink, Plus, SearchX, TriangleAlert } from 'lucide-react'
import { Badge, Button, Card, EmptyState, Loading, PlaceholderImage } from '../components/common'
import { WalkTimeCard } from '../components/detail/WalkTimeCard'
import { PageContainer } from '../components/layout/PageContainer'
import { MAX_COMPARE, useCompare } from '../contexts/CompareContext'
import { useProfile } from '../contexts/ProfileContext'
import { getPropertyById } from '../services/propertyService'
import type { Property } from '../types/property'
import { getMonthlyFixedCost, isWithinBudget } from '../utils/cost'
import { formatArea, formatManwon, NO_INFO } from '../utils/format'

// 어떤 ID에 대한 결과인지 함께 들고 있어, ID가 바뀌면 렌더 중에 로딩으로 판단한다.
type LoadState = { id: string } & ({ kind: 'ready'; property: Property | null } | { kind: 'error' })

export function PropertyDetailPage() {
  const { id = '' } = useParams()
  const [result, setResult] = useState<LoadState | null>(null)

  useEffect(() => {
    let cancelled = false
    getPropertyById(id)
      .then((property) => !cancelled && setResult({ id, kind: 'ready', property }))
      .catch(() => !cancelled && setResult({ id, kind: 'error' }))
    return () => {
      cancelled = true
    }
  }, [id])

  const load = result?.id === id ? result : null
  if (load === null) {
    return (
      <PageContainer>
        <Loading message="매물 정보를 불러오는 중입니다…" className="py-12" />
      </PageContainer>
    )
  }
  if (load.kind === 'error' || load.property === null) {
    return (
      <PageContainer>
        <EmptyState
          icon={load.kind === 'error' ? TriangleAlert : SearchX}
          title={load.kind === 'error' ? '매물 정보를 불러오지 못했습니다' : `매물 ${id}을(를) 찾을 수 없습니다`}
          action={<BackLink />}
        />
      </PageContainer>
    )
  }
  return <PropertyDetail property={load.property} />
}

function BackLink() {
  return (
    <Link to="/" className="inline-flex items-center gap-1 text-sm font-medium text-muted hover:text-text">
      <ArrowLeft className="size-4" aria-hidden />
      매물 목록으로
    </Link>
  )
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-4 border-b border-border py-3 last:border-b-0">
      <dt className="w-24 shrink-0 text-sm text-muted">{label}</dt>
      <dd className="min-w-0 flex-1 text-sm font-medium break-keep text-text">{children}</dd>
    </div>
  )
}

function PropertyDetail({ property: p }: { property: Property }) {
  const { profile } = useProfile()

  return (
    <PageContainer>
      <BackLink />

      <div className="mt-4 grid gap-5 lg:grid-cols-[1fr_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          <Card padding="none" className="overflow-hidden">
            <PlaceholderImage className="h-48 w-full md:h-64" />
            <div className="p-5">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone="blue">{p.property_type}</Badge>
                <Badge tone="gray">{p.property_id}</Badge>
              </div>
              <h1 className="mt-2 text-xl font-bold break-keep text-navy md:text-2xl">{p.address}</h1>
              <p className="mt-1 text-sm text-muted">{p.university} 인근</p>
            </div>
          </Card>

          <Card>
            <h2 className="text-base font-bold text-navy">기본 정보</h2>
            <dl className="mt-2">
              <InfoRow label="주소">{p.address}</InfoRow>
              <InfoRow label="전용면적">{formatArea(p.area_m2)}</InfoRow>
              <InfoRow label="주거 형태">{p.property_type}</InfoRow>
              <InfoRow label="매물 확인일">{p.listing_checked_at ?? NO_INFO}</InfoRow>
            </dl>
          </Card>

          <WalkTimeCard property={p} visits={profile.visits} />
        </div>

        <div className="flex flex-col gap-5 lg:sticky lg:top-24 lg:self-start">
          <CostCard property={p} budget={profile.budgetManwon} />
          <ActionCard property={p} />
        </div>
      </div>
    </PageContainer>
  )
}

function CostCard({ property: p, budget }: { property: Property; budget: number | null }) {
  const cost = getMonthlyFixedCost(p)
  const within = budget === null ? null : isWithinBudget(p, budget)

  return (
    <Card>
      <h2 className="text-base font-bold text-navy">비용</h2>

      <div className="mt-4 rounded-control bg-navy px-4 py-4 text-white">
        <p className="text-xs text-white/70">월 고정 주거비 (월세 + 관리비)</p>
        <p className="mt-1 text-3xl font-bold">{formatManwon(cost)}</p>
        <div className="mt-2">
          {budget === null ? (
            <p className="text-xs text-white/70">
              <Link to="/profile" className="font-medium text-white underline">
                생활 프로필
              </Link>
              에서 월 예산을 입력하면 예산 이내 여부를 알려 드려요.
            </p>
          ) : within === null ? (
            <p className="text-xs text-white/70">비용 정보가 없어 예산과 비교할 수 없습니다.</p>
          ) : within ? (
            <Badge tone="green">
              <Check className="size-3" aria-hidden />
              예산 이내 (예산 {formatManwon(budget)})
            </Badge>
          ) : (
            <Badge tone="gray" className="bg-white text-navy">
              <TriangleAlert className="size-3" aria-hidden />
              예산 초과 (예산 {formatManwon(budget)})
            </Badge>
          )}
        </div>
      </div>

      <dl className="mt-3">
        <InfoRow label="보증금">{formatManwon(p.deposit_manwon)}</InfoRow>
        <InfoRow label="월세">{formatManwon(p.monthly_rent_manwon)}</InfoRow>
        <InfoRow label="관리비">{formatManwon(p.maintenance_manwon)}</InfoRow>
      </dl>
    </Card>
  )
}

function ActionCard({ property: p }: { property: Property }) {
  const compare = useCompare()
  const inCompare = compare.has(p.property_id)

  return (
    <Card className="flex flex-col gap-2.5">
      {inCompare ? (
        <Button variant="secondary" fullWidth onClick={() => compare.remove(p.property_id)}>
          <Check className="size-4 text-blue" aria-hidden />
          비교 목록에 담김 · 빼기
        </Button>
      ) : (
        <Button fullWidth disabled={compare.isFull} onClick={() => compare.add(p.property_id)}>
          <Plus className="size-4" aria-hidden />
          비교 목록에 추가
        </Button>
      )}
      {!inCompare && compare.isFull && (
        <p className="text-center text-xs text-muted">비교는 최대 {MAX_COMPARE}개까지 담을 수 있습니다.</p>
      )}
      {compare.ids.length > 0 && (
        <Link to="/compare" className="text-center text-xs font-medium text-blue hover:underline">
          비교 목록 보기 ({compare.ids.length}/{MAX_COMPARE})
        </Link>
      )}

      {p.source_url ? (
        <a
          href={p.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-10 items-center justify-center gap-1.5 rounded-control border border-border bg-white px-4 text-sm font-medium text-text hover:bg-bg"
        >
          {sourceSiteLabel(p.source_url)}에서 원본 보기
          <ExternalLink className="size-4" aria-hidden />
        </a>
      ) : (
        <p className="text-center text-xs text-muted">원본 링크 {NO_INFO}</p>
      )}
    </Card>
  )
}

// 원본 링크의 사이트 이름 (링크 주소는 받은 그대로 쓰고, 표시 이름만 고른다)
function sourceSiteLabel(url: string): string {
  let host = ''
  try {
    host = new URL(url).hostname
  } catch {
    return '원본 사이트'
  }
  if (host.endsWith('land.naver.com')) return '네이버 부동산'
  if (host.endsWith('zigbang.com')) return '직방'
  if (host.endsWith('dabangapp.com')) return '다방'
  if (host.endsWith('daangn.com')) return '당근'
  return '원본 사이트'
}
