import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Check, CircleAlert, Plus, Sparkles, TriangleAlert, UserRound } from 'lucide-react'
import { categoryInfo } from '../../constants/recommend'
import { useCompare } from '../../contexts/CompareContext'
import { API_URL, ApiError } from '../../services/api'
import { recommend, toRecommendRequest } from '../../services/recommendService'
import type { Property } from '../../types/property'
import type { RankedProperty, RecommendResult } from '../../types/recommend'
import { cx } from '../../utils/cx'
import { formatArea, formatManwon } from '../../utils/format'
import { missingForRecommend, type Profile } from '../../utils/profile'
import { Badge, Card, EmptyState } from '../common'

const PAGE = 20

const LINK_BUTTON =
  'inline-flex h-10 items-center gap-1.5 rounded-control bg-blue px-4 text-sm font-medium text-white hover:bg-blue/90'
const LINK_SECONDARY =
  'inline-flex h-8 items-center rounded-control border border-border bg-white px-3 text-sm font-medium text-text hover:bg-bg'

const pct = (w: number) => `${Math.round(w * 100)}%`

type State = { status: 'loading' } | { status: 'error'; message: string } | { status: 'done'; result: RecommendResult }
type SortMode = 'ai' | 'price'

interface RecommendPanelProps {
  profile: Profile
  isSaved: boolean
  properties: Property[]
}

// 추천 결과 (BE POST /recommend, v3 카테고리). 가격 범위 안 매물 전체를 보여 주고 TOP K 를 강조한다.
// 점수는 전부 BE 가 계산하고 여기서는 보여 주기만 한다.
export function RecommendPanel({ profile, isSaved, properties }: RecommendPanelProps) {
  const missing = missingForRecommend(profile)
  const canRequest = API_URL !== null && isSaved && missing.length === 0
  const requestKey = canRequest ? JSON.stringify(toRecommendRequest(profile)) : null

  // 응답은 요청 키와 함께 저장한다. 지금 요청 키의 응답이 아직 없으면 계산 중이다.
  const [response, setResponse] = useState<{ key: string; state: State } | null>(null)
  const state: State = response && response.key === requestKey ? response.state : { status: 'loading' }

  useEffect(() => {
    if (requestKey === null) return
    const ctrl = new AbortController()
    recommend(JSON.parse(requestKey), ctrl.signal)
      .then((result) => setResponse({ key: requestKey, state: { status: 'done', result } }))
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return
        const message = e instanceof ApiError ? e.message : '추천을 불러오지 못했습니다.'
        setResponse({ key: requestKey, state: { status: 'error', message } })
      })
    return () => ctrl.abort()
  }, [requestKey])

  const byId = useMemo(() => new Map(properties.map((p) => [p.property_id, p])), [properties])

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">추천 결과</h2>
        <Badge tone="gray">이대 매물 전체 대상</Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        예산 안 매물을 모두 보여 주고, 고른 기준으로 매긴 상위 매물을 강조합니다. 기준별 점수는 매물마다 0~100점입니다.
      </p>

      {!isSaved || missing.length > 0 ? (
        <EmptyState
          icon={UserRound}
          title="생활 프로필을 먼저 채워 주세요"
          description={
            isSaved ? `추천에 필요한 입력: ${missing.join(', ')}` : '학과·학년·유지비 상한과 중요한 기준을 고르고 저장하면 추천을 볼 수 있어요.'
          }
          action={
            <Link to="/profile" className={LINK_BUTTON}>
              생활 프로필로 가기
            </Link>
          }
        />
      ) : API_URL === null ? (
        <EmptyState
          icon={TriangleAlert}
          title="추천 서버 연결이 필요합니다"
          description="FE/.env.local 에 VITE_API_URL(예: http://localhost:8000)을 설정하고 BE 서버를 실행해 주세요."
        />
      ) : state.status === 'loading' ? (
        <p className="py-10 text-center text-sm text-muted">추천을 계산하는 중입니다…</p>
      ) : state.status === 'error' ? (
        <EmptyState icon={TriangleAlert} title="추천을 불러오지 못했습니다" description={state.message} />
      ) : (
        <ResultView result={state.result} profile={profile} byId={byId} />
      )}
    </Card>
  )
}

function ResultView({ result, profile, byId }: { result: RecommendResult; profile: Profile; byId: Map<string, Property> }) {
  const [sort, setSort] = useState<SortMode>('ai')
  const [limit, setLimit] = useState(PAGE)
  const rows = useMemo(() => {
    if (sort === 'ai') return result.ranked
    return [...result.ranked].sort(
      (a, b) => a.upkeep_manwon - b.upkeep_manwon || (a.deposit_manwon ?? 0) - (b.deposit_manwon ?? 0) || a.rank - b.rank,
    )
  }, [result.ranked, sort])

  return (
    <div className="mt-4 flex flex-col gap-4">
      {/* 조건·기준 요약 */}
      <div className="flex flex-col gap-3 rounded-control border border-border px-4 py-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-bold text-navy">조건</span>
          {profile.depositBudgetManwon !== null && <Badge>보증금 ≤ {formatManwon(profile.depositBudgetManwon)}</Badge>}
          <Badge>유지비 ≤ {formatManwon(profile.budgetManwon)} / 월</Badge>
          <span className="font-bold text-blue">→ {result.ranked.length}개</span>
          <Link to="/profile" className={cx(LINK_SECONDARY, 'ml-auto')}>
            조건·기준 바꾸기
          </Link>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-bold text-navy">기준</span>
          {result.categories.map((k, i) => (
            <span
              key={k}
              className={cx(
                'flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-1',
                result.weights[k] > 0 ? 'bg-blue-50 text-blue' : 'bg-bg text-muted line-through',
              )}
            >
              <span className="flex size-5 items-center justify-center rounded-full bg-navy text-xs font-bold text-white no-underline">
                {i + 1}
              </span>
              {categoryInfo(k)?.label} {pct(result.weights[k] ?? 0)}
            </span>
          ))}
        </div>
      </div>

      {result.notes.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-control bg-amber-50 px-4 py-3 text-xs text-amber">
          {result.notes.map((n) => (
            <li key={n} className="flex items-start gap-1.5">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {n}
            </li>
          ))}
        </ul>
      )}

      {/* 정렬 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-control bg-bg p-1" role="group" aria-label="정렬">
          {(
            [
              ['ai', 'AI 추천순'],
              ['price', '가격 낮은 순'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={sort === key}
              onClick={() => setSort(key)}
              className={cx(
                'rounded-[9px] px-3.5 py-1.5 text-sm',
                sort === key ? 'bg-white font-bold text-navy shadow-sm' : 'font-medium text-muted',
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <span className="size-3.5 rounded border border-blue bg-blue-50" aria-hidden />
          추천 상위 {result.top_k}개 강조
        </span>
      </div>

      {rows.length === 0 ? (
        <EmptyState icon={CircleAlert} title="조건에 맞는 매물이 없습니다" description="유지비·보증금 상한을 바꿔 보세요." />
      ) : (
        <ol className="flex flex-col gap-2.5">
          {rows.slice(0, limit).map((r) => (
            <ResultRow key={r.property_id} item={r} categories={result.categories} property={byId.get(r.property_id)} />
          ))}
        </ol>
      )}
      {rows.length > limit && (
        <button type="button" onClick={() => setLimit((n) => n + PAGE)} className="self-center text-sm font-medium text-blue">
          {rows.length}개 중 {limit}개 표시 · 더 보기
        </button>
      )}

      {result.excluded.length > 0 && (
        <details className="rounded-control border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text">
            추천에서 제외된 매물 {result.excluded.length}개
          </summary>
          <ul className="max-h-72 divide-y divide-border overflow-y-auto border-t border-border text-sm">
            {result.excluded.map((e) => (
              <li key={e.property_id} className="flex flex-wrap gap-x-3 px-4 py-2">
                <span className="font-medium text-text">{byId.get(e.property_id)?.address ?? e.property_id}</span>
                <span className="text-muted">{e.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="flex items-start gap-1.5 text-xs text-muted">
        <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        광고 매물이라 실제 입주 가능 여부는 원본 링크에서 확인해 주세요. 도보시간은 Kakao 도보 경로(강의동 4곳, 건물 대표 지점) 기준입니다.
      </p>
    </div>
  )
}

function ResultRow({ item, categories, property }: { item: RankedProperty; categories: string[]; property: Property | undefined }) {
  const compare = useCompare()
  const inCompare = compare.has(item.property_id)
  const top = item.top

  return (
    <li
      className={cx(
        'flex flex-wrap items-center gap-x-4 gap-y-3 rounded-control border px-4 py-3.5',
        top ? 'border-blue bg-blue-50' : 'border-border bg-white',
        item.rank === 1 && top && 'border-2',
      )}
    >
      <span
        className={cx(
          'flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold',
          top ? 'bg-navy text-white' : 'bg-bg text-muted',
        )}
      >
        {item.rank}
      </span>

      <div className="min-w-48 flex-1">
        <p className="flex flex-wrap items-center gap-1.5">
          <Link to={`/properties/${item.property_id}`} className="text-[15px] font-bold text-text hover:underline">
            {property?.address ?? item.property_id}
          </Link>
          {property && <Badge tone="blue">{property.property_type}</Badge>}
        </p>
        <p className="mt-0.5 text-xs text-muted">
          보증금 {formatManwon(item.deposit_manwon)} · 유지비 {formatManwon(item.upkeep_manwon)} / 월
          {property?.area_m2 != null && ` · ${formatArea(property.area_m2)}`}
        </p>
      </div>

      <ul className={cx('flex flex-col gap-1', top ? 'w-72' : 'w-64')}>
        {categories.map((k) => {
          const s = item.category_scores[k]
          const v = item.category_values[k]
          return (
            <li key={k} className="flex items-center gap-2 text-xs">
              <span className="w-14 shrink-0 text-muted">{categoryInfo(k)?.short}</span>
              {s === null || s === undefined ? (
                <span className="text-muted">{categoryInfo(k)?.ready ? '계산 불가' : '데이터 준비 중'}</span>
              ) : (
                <>
                  <span className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-white" aria-hidden>
                    <span className="block h-full rounded-full bg-blue" style={{ width: `${Math.max(0, Math.min(100, s))}%` }} />
                  </span>
                  <span className="font-medium text-text">{v}</span>
                </>
              )}
            </li>
          )
        })}
      </ul>

      <p className="w-16 text-right">
        {item.score === null ? (
          <span className="text-xs text-muted">점수 없음</span>
        ) : (
          <>
            <span className={cx('font-bold', top ? 'text-xl text-navy' : 'text-base text-text')}>{item.score}</span>
            <span className="text-[11px] text-muted">/100</span>
          </>
        )}
      </p>

      <button
        type="button"
        onClick={() => compare.toggle(item.property_id)}
        disabled={!inCompare && compare.isFull}
        className="inline-flex h-8 items-center gap-1 rounded-control border border-border bg-white px-3 text-xs font-medium text-text hover:bg-bg disabled:cursor-not-allowed disabled:text-muted"
      >
        {inCompare ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}
        {inCompare ? '비교 중' : '비교'}
      </button>
    </li>
  )
}
