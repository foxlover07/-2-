import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowDown, ArrowUp, MessageCircle, RotateCcw, Send, Sparkles, X } from 'lucide-react'
import { factorLabel } from '../../constants/recommend'
import { isUniversityName } from '../../constants/universities'
import { useProfile } from '../../contexts/ProfileContext'
import { useUniversity } from '../../contexts/UniversityContext'
import { API_URL, ApiError } from '../../services/api'
import { chat, fromAgentProfile, toAgentProfile } from '../../services/agentService'
import type { AgentAnalysis, AgentComparison, AgentRecSummary, ChatResponse, PolicyResult } from '../../types/agent'
import type { Amenities, BuildingInfo, MarketCompare } from '../../types/market'
import { cx } from '../../utils/cx'
import { formatManwon } from '../../utils/format'
import { Badge, Button, Loading } from '../common'

// AI 주거 상담 (BE POST /agent/chat). 점수·순위는 BE 통합안 엔진이 정하고, 여기서는 보여 주기만 한다.
// 분석(Pareto·Trade-off·민감도·조건 완화·실거래·편의시설)은 참고 정보이며 순위를 바꾸지 않는다.
// 챗봇이 바꾼 조건은 생활 프로필에 저장해 폼·추천 화면과 같은 조건을 쓴다. 가정(what-if)은 저장하지 않는다.

const SESSION_KEY = 'home-poc:agent-session'
const EXAMPLES = [
  '이화여대 공대생이야. 월세랑 관리비 합쳐서 70만 원 이하, 통학이 1순위고 그다음 돈. 걸어서 15분, 18㎡ 정도.',
  '예산을 10만 원 올리면 어떻게 달라져?',
  '1위 월세가 주변 실거래보다 비싼 편이야?',
  '돈을 더 내면 통학이 얼마나 줄어?',
  '청년 월세 지원 같은 주거 정책 알려 줘',
]

interface Turn {
  id: number
  role: 'user' | 'assistant'
  text: string
  res?: ChatResponse
  error?: boolean
}

function newSessionId(): string {
  const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : String(Date.now())
  try {
    sessionStorage.setItem(SESSION_KEY, id)
  } catch {
    // 저장 실패해도 메모리 값으로 계속
  }
  return id
}

function initialSessionId(): string {
  try {
    return sessionStorage.getItem(SESSION_KEY) ?? newSessionId()
  } catch {
    return newSessionId()
  }
}

const label = factorLabel

export function ChatPanel() {
  const { profile, isSaved, saveProfile } = useProfile()
  const { university, setUniversity } = useUniversity()
  const [open, setOpen] = useState(false)
  const [sessionId, setSessionId] = useState(initialSessionId)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const nextId = useRef(1)
  const listRef = useRef<HTMLDivElement>(null)
  // 비교 결과에서 매물 ID → 주소 (대화 중 받은 추천 결과에서 모은다)
  const addresses = useMemo(() => {
    const m = new Map<string, string>()
    for (const t of turns) for (const p of t.res?.recommendation_results?.top ?? []) if (p.address) m.set(p.property_id, p.address)
    return m
  }, [turns])

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, busy])

  const push = (t: Omit<Turn, 'id'>) => setTurns((ts) => [...ts, { ...t, id: nextId.current++ }])

  const send = async (text: string) => {
    const message = text.trim()
    if (!message || busy) return
    setInput('')
    push({ role: 'user', text: message })
    setBusy(true)
    try {
      const res = await chat(sessionId, message, toAgentProfile(profile, isSaved, university))
      // 챗봇이 실제로 바꾼 조건만 생활 프로필에 저장 (가정·질문 응답은 저장하지 않음)
      if (res.profile_changed && res.updated_profile) {
        saveProfile(fromAgentProfile(res.updated_profile, profile))
        const u = res.updated_profile.university
        if (u && isUniversityName(u) && u !== university) setUniversity(u)
      }
      push({ role: 'assistant', text: res.assistant_message, res })
    } catch (e) {
      push({
        role: 'assistant',
        text: e instanceof ApiError ? `요청에 실패했습니다: ${e.message}` : '요청에 실패했습니다. 잠시 뒤 다시 시도해 주세요.',
        error: true,
      })
    } finally {
      setBusy(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  const reset = () => {
    setSessionId(newSessionId())
    setTurns([])
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed right-4 bottom-4 z-40 inline-flex h-12 items-center gap-2 rounded-full bg-navy px-5 text-sm font-medium text-white shadow-lg hover:bg-navy/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue"
      >
        <MessageCircle className="size-5" aria-hidden />
        AI 상담
      </button>
    )
  }

  return (
    <section
      aria-label="AI 주거 상담"
      className="fixed right-4 bottom-4 z-40 flex h-[min(40rem,calc(100svh-5rem))] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-card border border-border bg-white shadow-xl"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Sparkles className="size-4 text-blue" aria-hidden />
        <h2 className="text-sm font-bold text-navy">AI 주거 상담</h2>
        <Badge tone="gray">추천 계산은 추천 엔진</Badge>
        <div className="ml-auto flex gap-1">
          <Button variant="ghost" size="sm" onClick={reset} aria-label="새 대화" title="새 대화">
            <RotateCcw className="size-4" aria-hidden />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)} aria-label="닫기" title="닫기">
            <X className="size-4" aria-hidden />
          </Button>
        </div>
      </header>

      <div ref={listRef} className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {API_URL === null && (
          <p className="rounded-control bg-amber-50 p-3 text-xs text-amber">
            서버 주소(VITE_API_URL)가 없어 AI 상담을 쓸 수 없습니다.
          </p>
        )}
        {turns.length === 0 && (
          <div className="text-sm text-muted">
            <p>학교, 월 유지비(월세+관리비) 상한, 우선순위(이동 시간·예산·면적), 희망 통학 시간·면적, 수업 듣는 건물을 자연어로 알려 주세요.</p>
            <p className="mt-1 text-xs">저장된 생활 프로필이 있으면 그 조건에서 시작합니다. 바뀐 조건은 생활 프로필에 저장돼요.</p>
            <div className="mt-3 flex flex-col gap-1.5">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => void send(ex)}
                  disabled={busy || API_URL === null}
                  className="rounded-control border border-border px-3 py-2 text-left text-xs text-text hover:bg-bg disabled:text-muted"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((t) =>
          t.role === 'user' ? (
            <p key={t.id} className="max-w-[85%] self-end rounded-card bg-blue px-3 py-2 text-sm whitespace-pre-line text-white">
              {t.text}
            </p>
          ) : (
            <AssistantTurn
              key={t.id}
              turn={t}
              addresses={addresses}
              onAnswer={t.id === turns[turns.length - 1]?.id && !busy ? (a) => void send(a) : undefined}
            />
          ),
        )}
        {busy && <Loading message="조건을 정리하고 추천을 계산하는 중…" className="self-start text-left" />}
      </div>

      <form onSubmit={onSubmit} className="flex gap-2 border-t border-border p-3">
        <label className="sr-only" htmlFor="agent-input">
          메시지
        </label>
        <input
          id="agent-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="예: 예산을 80만 원으로 바꾸면?"
          maxLength={2000}
          disabled={API_URL === null}
          className="h-10 min-w-0 flex-1 rounded-control border border-border px-3 text-sm focus-visible:outline-2 focus-visible:outline-blue"
        />
        <Button type="submit" disabled={busy || !input.trim() || API_URL === null} aria-label="보내기">
          <Send className="size-4" aria-hidden />
        </Button>
      </form>
    </section>
  )
}

const STATUS_BADGE: Partial<Record<ChatResponse['status'], { tone: 'amber' | 'red'; text: string }>> = {
  need_info: { tone: 'amber', text: '정보 필요' },
  confirm: { tone: 'amber', text: '확인 필요' },
  llm_unavailable: { tone: 'red', text: 'AI 설정 안 됨' },
  llm_error: { tone: 'red', text: 'AI 응답 실패' },
  error: { tone: 'red', text: '오류' },
}

function AssistantTurn({
  turn,
  addresses,
  onAnswer,
}: {
  turn: Turn
  addresses: Map<string, string>
  onAnswer?: (answer: string) => void // 마지막 확인 질문에만 (네 / 아니요 버튼)
}) {
  const res = turn.res
  const badge = res ? STATUS_BADGE[res.status] : turn.error ? { tone: 'red' as const, text: '연결 실패' } : undefined
  return (
    <div className="flex max-w-[95%] flex-col gap-2 self-start">
      <div className={cx('rounded-card px-3 py-2 text-sm whitespace-pre-line', turn.error ? 'bg-red-50 text-red' : 'bg-bg text-text')}>
        {badge && (
          <Badge tone={badge.tone} className="mb-1">
            {badge.text}
          </Badge>
        )}
        <p>{turn.text}</p>
      </div>
      {res?.status === 'confirm' && onAnswer && (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => onAnswer('네')}>
            네, 바꿀게요
          </Button>
          <Button size="sm" variant="secondary" onClick={() => onAnswer('아니요')}>
            아니요
          </Button>
        </div>
      )}
      {res?.profile_changed && (
        <p className="text-xs text-green">
          바뀐 조건을 생활 프로필에 저장했어요.{' '}
          <Link to="/compare" className="underline">
            매물 비교에서 전체 결과 보기
          </Link>
        </p>
      )}
      {res?.recommendation_results && <ResultCard rec={res.recommendation_results} whatIf={res.intent === 'what_if'} />}
      {res?.comparison_results && <ComparisonCard cmp={res.comparison_results} addresses={addresses} />}
      {res?.analysis_results?.result && <AnalysisCard an={res.analysis_results} addresses={addresses} />}
      {res?.policy_results && <PolicyCard pol={res.policy_results} />}
    </div>
  )
}

function ResultCard({ rec, whatIf }: { rec: AgentRecSummary; whatIf: boolean }) {
  return (
    <div className="rounded-card border border-border p-3">
      <p className="text-xs text-muted">
        {whatIf ? '가정한 조건' : '지금 조건'} · 후보 {rec.total_ranked}개 ·{' '}
        {rec.priorities.map((k, i) => `${i + 1}순위 ${label(k)} ${Math.round((rec.weights[k] ?? 0) * 100)}%`).join(', ')}
      </p>
      <ol className="mt-2 flex flex-col gap-2">
        {rec.top.map((t) => (
          <li key={t.property_id} className="flex items-start gap-2 text-sm">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-navy text-xs text-white">
              {t.rank}
            </span>
            <div className="min-w-0 flex-1">
              <Link to={`/properties/${encodeURIComponent(t.property_id)}`} className="font-medium text-navy hover:underline">
                {t.address ?? t.property_id}
              </Link>
              <p className="text-xs text-muted">
                유지비 {formatManwon(t.monthly_cost_manwon)} / 월{t.over_budget && ' (예산 초과)'} · 보증금{' '}
                {formatManwon(t.deposit_manwon)} · {t.score}점
              </p>
              <p className="text-xs text-muted">
                통학 편도 {t.travel_min === null ? '정보 없음' : `${t.travel_min}분`} · 면적 {t.area_m2}㎡
              </p>
              {t.same_house_ids.length > 0 && (
                <p className="text-xs text-muted">같은 집일 수 있는 광고 {t.same_house_ids.length}건 더 (묶어서 하나만 보여 줌)</p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

function ComparisonCard({ cmp, addresses }: { cmp: AgentComparison; addresses: Map<string, string> }) {
  return (
    <div className="rounded-card border border-border p-3 text-sm">
      <p className="text-xs text-muted">
        {cmp.label_before} {cmp.total_before}개 → {cmp.label_after} {cmp.total_after}개
      </p>
      <ul className="mt-2 flex flex-col gap-1">
        {cmp.score_changes.map((c) => {
          const up = c.rank_before !== null && c.rank_after !== null && c.rank_after < c.rank_before
          const down = c.rank_before !== null && c.rank_after !== null && c.rank_after > c.rank_before
          return (
            <li key={c.property_id} className="flex items-center gap-1.5 text-xs">
              {up ? (
                <ArrowUp className="size-3.5 text-green" aria-label="순위 오름" />
              ) : down ? (
                <ArrowDown className="size-3.5 text-red" aria-label="순위 내림" />
              ) : (
                <span className="inline-block size-3.5" />
              )}
              <span className="min-w-0 flex-1 truncate">{addresses.get(c.property_id) ?? c.property_id}</span>
              <span className="shrink-0 text-muted">
                {c.rank_before === null ? '순위 밖' : `${c.rank_before}위`} →{' '}
                {c.rank_after === null ? '순위 밖' : `${c.rank_after}위`}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// ---------------------------------------------------------------- 분석 결과 (참고, 순위 안 바뀜)

interface Summ {
  property_id: string
  rank: number
  monthly_cost_manwon: number
  travel_min: number | null
  area_m2: number
}

const ANALYSIS_TITLE: Record<AgentAnalysis['kind'], string> = {
  pareto: 'Pareto 분석',
  tradeoff: '비용·통학·면적 Trade-off',
  sensitivity: '조건 민감도',
  relaxation: '조건 완화 (가정)',
  market: '주변 전월세 실거래 비교',
  amenities: '주변 생활 편의시설',
  building: '건축물대장 정보',
}

function AnalysisCard({ an, addresses }: { an: AgentAnalysis; addresses: Map<string, string> }) {
  const name = (id: string | null | undefined) => (id ? (addresses.get(id) ?? id) : '')
  const r = an.result as Record<string, unknown>
  let body: ReactNode = null
  if (an.kind === 'pareto') {
    const top = r.top_k as Array<Summ & { pareto: boolean; dominated_by: string | null }>
    body = (
      <>
        <p className="text-xs text-muted">
          후보 {String(r.candidates)}개 중 모든 면에서 다른 매물보다 못하지 않은 매물 {(r.frontier as unknown[]).length}개
        </p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs">
          {top.map((t) => (
            <li key={t.property_id}>
              {t.rank}위 {name(t.property_id)} —{' '}
              {t.pareto ? <span className="text-green">해당</span> : <span className="text-muted">{name(t.dominated_by)}보다 못함</span>}
            </li>
          ))}
        </ul>
      </>
    )
  } else if (an.kind === 'tradeoff') {
    const ref = r.reference as Summ
    const steps = r.pay_more_for_commute as Array<{ extra_budget_manwon: number; best: Summ | null; travel_saved_min: number | null }>
    body = (
      <>
        <p className="text-xs text-muted">
          기준 {ref.rank}위 · 월 {ref.monthly_cost_manwon}만 원 · 통학 {ref.travel_min ?? '?'}분
        </p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs">
          {steps
            .filter((st) => st.best && st.travel_saved_min)
            .map((st) => (
              <li key={st.extra_budget_manwon}>
                +{st.extra_budget_manwon}만 원까지: {name(st.best!.property_id)} (통학 {st.travel_saved_min}분 단축)
              </li>
            ))}
        </ul>
      </>
    )
  } else if (an.kind === 'sensitivity') {
    const kinds = r.by_kind as Array<{ kind: string; label: string; mean_top_retention: number | null }>
    body = (
      <ul className="flex flex-col gap-1 text-xs">
        {kinds.map((k) => (
          <li key={k.kind} className="flex justify-between gap-2">
            <span>{k.label}을(를) 바꾸면</span>
            <span className="text-muted">
              상위 유지 {k.mean_top_retention === null ? '-' : `${Math.round(k.mean_top_retention * 100)}%`}
            </span>
          </li>
        ))}
      </ul>
    )
  } else if (an.kind === 'relaxation') {
    const minimal = r.minimal as Array<{ label: string; candidates: number; added: number }>
    body = (
      <>
        <p className="text-xs text-muted">지금 후보 {String(r.baseline_candidates)}개</p>
        <ul className="mt-1.5 flex flex-col gap-1 text-xs">
          {minimal.map((o) => (
            <li key={o.label}>
              {o.label} → 후보 {o.candidates}개 (+{o.added})
            </li>
          ))}
        </ul>
      </>
    )
  } else if (an.kind === 'market') {
    const m = r as unknown as MarketCompare
    body = m.available ? (
      <p className="text-xs">
        {name(an.property_id)}: 월세 {formatManwon(m.listing_monthly_rent_manwon)} · 실거래 {m.n}건 중앙값{' '}
        {formatManwon(m.rent_median)} · <b>{m.level ?? '수준 판단 안 함'}</b>
      </p>
    ) : (
      <p className="text-xs text-muted">{m.reason}</p>
    )
  } else if (an.kind === 'building') {
    const b = r as unknown as BuildingInfo
    body = b.available ? (
      <p className="text-xs">
        {name(an.property_id)}: 사용승인 {b.use_approval_date ?? '정보 없음'} · 지상 {b.floors_above ?? '?'}층 · 승강기{' '}
        {b.elevators ?? '?'}대 · 주차 {b.parking ?? '?'}대 · {b.main_purpose ?? '주용도 정보 없음'}
      </p>
    ) : (
      <p className="text-xs text-muted">{b.reason}</p>
    )
  } else if (an.kind === 'amenities') {
    const a = r as unknown as Amenities
    body = a.available ? (
      <p className="text-xs">
        {a.categories
          .filter((c) => c.group !== 'context')
          .map((c) => `${c.label} ${c.nearest ? `${c.nearest.distance_m}m` : '없음'}`)
          .join(' · ')}
      </p>
    ) : (
      <p className="text-xs text-muted">{a.reason}</p>
    )
  }
  return (
    <div className="rounded-card border border-border p-3 text-sm">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-xs font-bold text-navy">{ANALYSIS_TITLE[an.kind]}</span>
        <Badge tone="gray">참고 · 순위 그대로</Badge>
      </div>
      {body}
      {an.property_id && (
        <Link to={`/properties/${encodeURIComponent(an.property_id)}`} className="mt-1.5 inline-block text-xs text-blue underline">
          매물 상세에서 자세히 보기
        </Link>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- 주거 정책 안내 (자격 판정 없음)

function PolicyCard({ pol }: { pol: PolicyResult }) {
  return (
    <div className="rounded-card border border-border p-3 text-sm">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-xs font-bold text-navy">주거 지원 정책 안내</span>
        <Badge tone="gray">자격 판정 아님</Badge>
      </div>
      {!pol.available || pol.items.length === 0 ? (
        <p className="text-xs text-muted">{pol.reason ?? `'${pol.query}' 관련 정책을 찾지 못했습니다.`}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {pol.items.map((it) => {
            const link = it.apply_url ?? it.ref_url
            return (
              <li key={it.policy_id} className="rounded-control bg-bg px-2.5 py-2 text-xs">
                <p className="font-medium text-text">{it.name}</p>
                {(it.support ?? it.summary) && <p className="mt-0.5 line-clamp-2 text-muted">{it.support ?? it.summary}</p>}
                <p className="mt-0.5 text-muted">
                  {[it.agency, it.region, it.target_age && `연령 ${it.target_age} (공고 기준)`, it.apply_period && `신청 ${it.apply_period}`]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
                {link && (
                  <a href={link} target="_blank" rel="noopener noreferrer" className="mt-0.5 inline-block text-blue underline">
                    공식 안내·신청 페이지
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-muted">
        {pol.disclaimer} 출처: {pol.source}
        {pol.fetched_at && ` · 기준 ${pol.fetched_at.slice(0, 10)}`}
      </p>
      <p className="mt-1 flex flex-wrap gap-x-2 text-[11px]">
        {pol.official_links.map((l) => (
          <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer" className="text-blue underline">
            {l.name}
          </a>
        ))}
      </p>
    </div>
  )
}
