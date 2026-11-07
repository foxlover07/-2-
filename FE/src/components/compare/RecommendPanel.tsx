import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { CircleAlert, Info, Plus, Check, Sparkles, ThumbsDown, TriangleAlert, UserRound } from 'lucide-react'
import { FACTORS, TRAVEL_PART_LABELS, factorLabel, type Priorities } from '../../constants/recommend'
import { useCompare } from '../../contexts/CompareContext'
import { API_URL, ApiError } from '../../services/api'
import { recommend, toRecommendRequest } from '../../services/recommendService'
import type { Property } from '../../types/property'
import type { RankedProperty, RecommendResult } from '../../types/recommend'
import { formatManwon } from '../../utils/format'
import { missingForRecommend, type Profile } from '../../utils/profile'
import { Badge, Card, EmptyState } from '../common'
import { PriorityPicker } from '../profile/PriorityPicker'

const TOP_N = 3

const LINK_BUTTON =
  'inline-flex h-10 items-center gap-1.5 rounded-control bg-blue px-4 text-sm font-medium text-white hover:bg-blue/90'

const pct = (w: number) => `${Math.round(w * 100)}%`

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'done'; result: RecommendResult }

interface RecommendPanelProps {
  profile: Profile
  isSaved: boolean
  properties: Property[]
}

// 조건 기반 추천 (BE POST /recommend, 설계안 v2). 점수는 전부 BE 가 계산하고 여기서는 보여 주기만 한다.
export function RecommendPanel({ profile, isSaved, properties }: RecommendPanelProps) {
  const missing = missingForRecommend(profile)
  const [priorities, setPriorities] = useState<Priorities>(profile.priorities)
  // 프로필의 순위가 바뀌면 결과 화면의 순위도 따라간다
  const [prevSaved, setPrevSaved] = useState(profile.savedAt)
  if (prevSaved !== profile.savedAt) {
    setPrevSaved(profile.savedAt)
    setPriorities(profile.priorities)
  }

  const canRequest = API_URL !== null && isSaved && missing.length === 0
  const requestKey = canRequest ? JSON.stringify(toRecommendRequest(profile, priorities)) : null
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
        <h2 className="text-base font-bold text-navy">조건 기반 추천 TOP {TOP_N}</h2>
        <Badge tone="gray">이대 매물 전체 대상</Badge>
      </div>
      <p className="mt-1 text-sm text-muted">
        생활 프로필의 학과 건물·시간표·예산과 우선순위로 계산합니다. 각 요소는 매물별 절대 점수(0~100)입니다.
      </p>

      {!isSaved || missing.length > 0 ? (
        <EmptyState
          icon={UserRound}
          title="생활 프로필을 먼저 채워 주세요"
          description={
            isSaved ? `추천에 필요한 입력: ${missing.join(', ')}` : '학과·학년·월 예산을 입력하고 저장하면 추천을 볼 수 있어요.'
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
      ) : (
        <>
          <div className="mt-4 rounded-control border border-border px-4 py-3">
            <PriorityPicker priorities={priorities} onChange={setPriorities} compact />
            {state.status === 'done' && (
              <p className="mt-2 text-xs text-muted">
                실제 적용 가중치:{' '}
                {FACTORS.map((f) => `${f.label} ${pct(state.result.weights[f.key] ?? 0)}`).join(' · ')}
              </p>
            )}
          </div>

          {state.status === 'loading' && (
            <p className="py-10 text-center text-sm text-muted">추천을 계산하는 중입니다…</p>
          )}
          {state.status === 'error' && (
            <EmptyState icon={TriangleAlert} title="추천을 불러오지 못했습니다" description={state.message} />
          )}
          {state.status === 'done' && <ResultView result={state.result} byId={byId} />}
        </>
      )}
    </Card>
  )
}

function ResultView({ result, byId }: { result: RecommendResult; byId: Map<string, Property> }) {
  const top = result.ranked.slice(0, TOP_N)
  const rest = result.ranked.slice(TOP_N)

  return (
    <div className="mt-4 flex flex-col gap-4">
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

      {top.length === 0 ? (
        <EmptyState icon={Info} title="조건에 맞는 매물이 없습니다" description="예산이나 학과 건물을 바꿔 보세요." />
      ) : (
        <ol className="grid gap-3 md:grid-cols-3">
          {top.map((r) => (
            <TopCard key={r.property_id} item={r} property={byId.get(r.property_id)} />
          ))}
        </ol>
      )}

      {rest.length > 0 && (
        <details className="rounded-control border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text">
            {TOP_N + 1}위 이하 {rest.length}개 보기
          </summary>
          <ul className="divide-y divide-border border-t border-border text-sm">
            {rest.map((r) => (
              <li key={r.property_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2">
                <span className="w-8 font-bold text-muted">{r.rank}</span>
                <Link to={`/properties/${r.property_id}`} className="font-medium text-text hover:underline">
                  {r.property_id}
                </Link>
                <span className="min-w-0 flex-1 truncate text-muted">{byId.get(r.property_id)?.address ?? ''}</span>
                {r.over_budget && <Badge tone="red">예산 초과</Badge>}
                <span className="text-muted">월 {formatManwon(r.monthly_cost_manwon)}</span>
                <span className="w-14 text-right font-bold text-navy">{r.score}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {result.not_recommended.length > 0 && (
        <div>
          <h3 className="flex items-center gap-1.5 text-sm font-bold text-navy">
            <ThumbsDown className="size-4" aria-hidden />
            다른 우선순위에서는 상위권인 매물
          </h3>
          <ul className="mt-2 flex flex-col gap-2">
            {result.not_recommended.map((n) => (
              <li key={n.property_id} className="rounded-control bg-bg px-4 py-3 text-sm">
                <p className="flex flex-wrap items-center gap-2">
                  <Link to={`/properties/${n.property_id}`} className="font-bold text-text hover:underline">
                    {n.property_id}
                  </Link>
                  <span className="text-xs text-muted">
                    {n.top_in
                      .map((c) => `${c.priorities.map(factorLabel).join('·')} 우선 시 ${c.rank}위`)
                      .join(' / ')}
                  </span>
                </p>
                <p className="mt-1 text-text">{n.message}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.excluded.length > 0 && (
        <details className="rounded-control border border-border">
          <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text">
            추천에서 제외된 매물 {result.excluded.length}개
          </summary>
          <ul className="divide-y divide-border border-t border-border text-sm">
            {result.excluded.map((e) => (
              <li key={e.property_id} className="flex flex-wrap gap-x-3 px-4 py-2">
                <span className="w-12 font-medium text-text">{e.property_id}</span>
                <span className="text-muted">{e.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <Assumptions assumptions={result.assumptions} />
    </div>
  )
}

function TopCard({ item, property }: { item: RankedProperty; property: Property | undefined }) {
  const compare = useCompare()
  const inCompare = compare.has(item.property_id)

  return (
    <li className="flex flex-col gap-3 rounded-control border border-border p-4">
      <div className="flex items-start gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-navy text-sm font-bold text-white">
          {item.rank}
        </span>
        <div className="min-w-0 flex-1">
          <Link to={`/properties/${item.property_id}`} className="text-sm font-bold text-text hover:underline">
            {item.property_id}
          </Link>
          <p className="truncate text-xs text-muted">{property?.address ?? '주소 정보 없음'}</p>
        </div>
        <p className="text-right">
          <span className="text-xl font-bold text-navy">{item.score}</span>
          <span className="text-xs text-muted"> / 100</span>
        </p>
      </div>

      <ul className="flex flex-col gap-1.5">
        {FACTORS.map((f) => (
          <FactorBar key={f.key} label={f.label} score={item.factor_scores[f.key] ?? null} />
        ))}
      </ul>

      <ul className="flex flex-col gap-0.5 text-xs text-muted">
        {Object.entries(item.travel_parts).map(([k, part]) => (
          <li key={k}>
            {TRAVEL_PART_LABELS[k] ?? k} 편도 평균{' '}
            <span className={part.avg_min > part.target_min ? 'font-medium text-red' : 'font-medium text-text'}>
              {part.avg_min}분
            </span>{' '}
            (희망 {part.target_min}분)
          </li>
        ))}
        <li>
          월 고정비 <span className="font-medium text-text">{formatManwon(item.monthly_cost_manwon)}</span> · 보증금{' '}
          {formatManwon(item.deposit_manwon)}
        </li>
      </ul>

      <div className="mt-auto flex flex-wrap items-center gap-2">
        {item.over_budget && <Badge tone="red">예산 초과</Badge>}
        <button
          type="button"
          onClick={() => compare.toggle(item.property_id)}
          disabled={!inCompare && compare.isFull}
          className="ml-auto inline-flex h-8 items-center gap-1 rounded-control border border-border bg-white px-3 text-xs font-medium text-text hover:bg-bg disabled:cursor-not-allowed disabled:text-muted"
        >
          {inCompare ? <Check className="size-3.5" aria-hidden /> : <Plus className="size-3.5" aria-hidden />}
          {inCompare ? '비교 중' : '비교에 담기'}
        </button>
      </div>
    </li>
  )
}

function FactorBar({ label, score }: { label: string; score: number | null }) {
  return (
    <li className="flex items-center gap-2 text-xs">
      <span className="w-16 shrink-0 text-muted">{label}</span>
      {score === null ? (
        <Badge tone="amber">데이터 준비 중</Badge>
      ) : (
        <>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg" aria-hidden>
            <span className="block h-full rounded-full bg-blue" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
          </span>
          <span className="w-10 text-right font-medium text-text">{Math.round(score)}</span>
        </>
      )}
    </li>
  )
}

function Assumptions({ assumptions: a }: { assumptions: Record<string, unknown> }) {
  const num = (k: string) => (typeof a[k] === 'number' ? (a[k] as number) : null)
  const buildings = Array.isArray(a.department_buildings) ? (a.department_buildings as string[]) : []
  const rows: Array<[string, string]> = [
    ['학과 건물', `${buildings.join(', ') || '-'} (${String(a.department_buildings_source ?? '-')})`],
    ['희망 통학 시간', `${num('desired_commute_min') ?? '-'}분 (도보, 편도)`],
    ['월 예산', `${formatManwon(num('budget_manwon'))} · 제외 기준 ${formatManwon(num('monthly_limit_manwon'))} 초과`],
    [
      '보증금 예산',
      num('deposit_budget_manwon') === null
        ? '미설정 (보증금 필터 없음)'
        : `${formatManwon(num('deposit_budget_manwon'))} · 제외 기준 ${formatManwon(num('deposit_limit_manwon'))} 초과`,
    ],
  ]
  if (typeof a.grade_note === 'string') rows.push(['학년', a.grade_note])

  return (
    <details className="rounded-control border border-border">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text">계산 가정 보기</summary>
      <dl className="grid gap-x-4 gap-y-1.5 border-t border-border px-4 py-3 text-xs sm:grid-cols-[7rem_1fr]">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="text-text">{v}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}
