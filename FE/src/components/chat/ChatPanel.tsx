import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowDown, ArrowUp, FileText, House, MessageCircle, RotateCcw, Send, X } from 'lucide-react'
import { factorLabel } from '../../constants/recommend'
import { isUniversityName } from '../../constants/universities'
import { useProfile } from '../../contexts/ProfileContext'
import { useUniversity } from '../../contexts/UniversityContext'
import { API_URL, ApiError } from '../../services/api'
import { chat, fromAgentProfile, toAgentProfile } from '../../services/agentService'
import type {
  AgentAnalysis,
  AgentComparison,
  AgentRecSummary,
  AgentTopItem,
  ChatMode,
  ChatResponse,
  ExcludedAnalysis,
  PolicyResult,
} from '../../types/agent'
import type { Amenities, BuildingInfo, MarketCompare } from '../../types/market'
import { cx } from '../../utils/cx'
import { formatManwon } from '../../utils/format'
import { Badge, Button, Loading } from '../common'

// AI 주거 상담 (BE POST /agent/chat). 점수·순위는 BE 통합안 엔진이 정하고, 여기서는 보여 주기만 한다.
// 분석(Pareto·Trade-off·민감도·조건 완화·실거래·편의시설)은 참고 정보이며 순위를 바꾸지 않는다.
// 챗봇이 바꾼 조건은 생활 프로필에 저장해 폼·추천 화면과 같은 조건을 쓴다. 가정(what-if)은 저장하지 않는다.

const SESSION_KEY = 'home-poc:agent-session'
const MODE_KEY = 'home-poc:agent-mode'
// 기능별 예시 프롬프트 (2026-11-15 팀 예시 반영). 정책은 안내만 하고 자격·유불리를 판단하지 않는다.
type ExampleGroup = { title: string; items: Array<{ label: string; text: string }> }
const EXAMPLE_GROUPS: Record<ChatMode, ExampleGroup[]> = {
  recommend: [
    {
      title: '생활 기능',
      items: [
        {
          label: '자주 가는 곳 반영',
          text: '알바하는 곳이 신촌역 근처라 주 3번 가. 거기까지 걷는 시간도 고려해서 월 고정비 80만원 이하 매물 3개 추천해줘.',
        },
        { label: '주변 환경 (소음)', text: '밤에 시끄러우면 잠을 못 자. 주변 150m 안에 술집이 많은 매물은 빼줘.' },
        { label: '생활 편의', text: '편의점이 걸어서 3분(200m) 안에 있고, 면적은 20㎡ 이상인 매물만 보여줘.' },
        { label: '우선순위 조정', text: '예산보다 통학이 더 중요해. 1순위는 이동 시간, 2순위는 넓은 면적으로 다시 추천해줘.' },
        { label: '비추천 이유', text: '추천에서 빠진 매물들은 각각 왜 빠졌는지 이유를 알려줘.' },
      ],
    },
  ],
  policy: [
    {
      title: '정책·계약',
      items: [
        {
          label: '정책 찾기',
          text: '만 20살 대학생이고, 부모님과 따로 살면서 월세 50만원 원룸에 살 예정이야. 소득은 알바로 월 80만원 정도인데, 내가 받을 수 있는 주거 지원 정책을 전부 알려줘.',
        },
        {
          label: '정책 비교',
          text: '청년 월세 지원이랑 버팀목 전세자금대출 중에 보증금 1000만원, 월세 60만원 원룸에 사는 대학생한테 뭐가 더 유리한지 비교해줘.',
        },
        { label: '신청 절차', text: '청년 월세 지원을 신청하려고 해. 필요한 서류, 신청하는 곳, 신청 순서를 단계별로 정리해줘.' },
        {
          label: '계약 안전 점검',
          text: '처음 자취방을 계약하는데, 전세사기나 보증금을 날리지 않으려면 계약 전·계약 당일·입주 후에 각각 뭘 확인해야해?',
        },
        { label: '지역 정책', text: '서울 서대문구에 사는 대학생이 받을 수 있는 서울시나 구 단위 청년 주거 지원이 따로 있어?' },
      ],
    },
  ],
}

// 모드별 화면 문구. 모드마다 대화(session_id)·대화 목록을 따로 둔다.
const MODES: Record<
  ChatMode,
  { label: string; icon: typeof House; badge: string; intro: string; sub: string; placeholder: string; loading: string }
> = {
  recommend: {
    label: '매물 추천',
    icon: House,
    badge: '추천 계산은 추천 엔진',
    intro: '학교, 월 유지비(월세+관리비) 상한, 우선순위(이동 시간·예산·면적), 희망 통학 시간·면적, 학과 건물을 자연어로 알려 주세요.',
    sub: '저장된 생활 프로필이 있으면 그 조건에서 시작합니다. 바뀐 조건은 생활 프로필에 저장돼요.',
    placeholder: '예: 예산을 80만 원으로 바꾸면?',
    loading: '조건을 정리하고 추천을 계산하는 중…',
  },
  policy: {
    label: '정책 안내',
    icon: FileText,
    badge: '공식 데이터 안내',
    intro: '주거 지원 정책, 신청 절차, 계약 전 확인 사항을 공식 데이터 기준으로 안내합니다.',
    sub: '대상자 여부는 판단하지 않아요. 자격·신청은 안내되는 공식 사이트에서 확인해 주세요. 이 모드는 생활 프로필을 바꾸지 않습니다.',
    placeholder: '예: 청년 월세 지원 신청 방법 알려줘',
    loading: '공식 정책 자료를 찾는 중…',
  },
}

function initialMode(): ChatMode {
  try {
    return sessionStorage.getItem(MODE_KEY) === 'policy' ? 'policy' : 'recommend'
  } catch {
    return 'recommend'
  }
}

interface Turn {
  id: number
  role: 'user' | 'assistant'
  text: string
  res?: ChatResponse
  error?: boolean
}

function sessionKey(mode: ChatMode): string {
  return `${SESSION_KEY}:${mode}`
}

function newSessionId(mode: ChatMode): string {
  const id = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : String(Date.now())
  try {
    sessionStorage.setItem(sessionKey(mode), id)
  } catch {
    // 저장 실패해도 메모리 값으로 계속
  }
  return id
}

function initialSessionId(mode: ChatMode): string {
  try {
    return sessionStorage.getItem(sessionKey(mode)) ?? newSessionId(mode)
  } catch {
    return newSessionId(mode)
  }
}

const label = factorLabel

export function ChatPanel() {
  const { profile, isSaved, saveProfile } = useProfile()
  const { university, setUniversity } = useUniversity()
  const [open, setOpen] = useState(false)
  const [mode, setModeState] = useState<ChatMode>(initialMode)
  const [sessionIds, setSessionIds] = useState<Record<ChatMode, string>>(() => ({
    recommend: initialSessionId('recommend'),
    policy: initialSessionId('policy'),
  }))
  const [turnsByMode, setTurnsByMode] = useState<Record<ChatMode, Turn[]>>({ recommend: [], policy: [] })
  const turns = turnsByMode[mode]
  const m = MODES[mode]
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

  // 응답이 늦게 와도 보낸 모드의 대화에 붙인다
  const pushTo = (to: ChatMode, t: Omit<Turn, 'id'>) =>
    setTurnsByMode((all) => ({ ...all, [to]: [...all[to], { ...t, id: nextId.current++ }] }))

  const setMode = (next: ChatMode) => {
    setModeState(next)
    try {
      sessionStorage.setItem(MODE_KEY, next)
    } catch {
      // 저장 실패해도 화면 상태로 계속
    }
  }

  const send = async (text: string, sentMode: ChatMode = mode) => {
    const message = text.trim()
    if (!message || busy) return
    const push = (t: Omit<Turn, 'id'>) => pushTo(sentMode, t)
    setInput('')
    push({ role: 'user', text: message })
    setBusy(true)
    try {
      const res = await chat(sessionIds[sentMode], message, toAgentProfile(profile, isSaved, university), sentMode)
      // 챗봇이 실제로 바꾼 조건만 생활 프로필에 저장 (가정·질문 응답은 저장하지 않음). 정책 모드는 저장하지 않는다.
      if (sentMode === 'recommend' && res.profile_changed && res.updated_profile) {
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

  // 추천 매물 → 정책 안내 모드로 넘겨 관련 정책을 묻는다 (자격 판정 없음, 매물 사실만 질문에 담는다)
  const askPolicyFor = (t: AgentTopItem) => {
    setMode('policy')
    void send(policyQuestion(t), 'policy')
  }

  const reset = () => {
    setSessionIds((ids) => ({ ...ids, [mode]: newSessionId(mode) }))
    setTurnsByMode((all) => ({ ...all, [mode]: [] }))
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
      <header className="flex items-center gap-2 border-b border-border px-3 py-2.5">
        <h2 className="sr-only">AI 주거 상담</h2>
        {/* 모드 배너: 매물 추천 / 정책 안내. 모드마다 대화가 따로 이어진다 */}
        <div role="tablist" aria-label="상담 모드" className="flex shrink-0 rounded-control bg-bg p-0.5">
          {(Object.keys(MODES) as ChatMode[]).map((k) => {
            const Icon = MODES[k].icon
            const active = k === mode
            return (
              <button
                key={k}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setMode(k)}
                disabled={busy && !active}
                title={busy && !active ? '답변을 받은 뒤 바꿀 수 있어요' : undefined}
                className={cx(
                  'inline-flex items-center gap-1 rounded-control px-2.5 py-1.5 text-xs whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-blue disabled:cursor-not-allowed disabled:opacity-50',
                  active ? 'bg-white font-bold text-navy shadow-sm' : 'font-medium text-muted hover:text-text',
                )}
              >
                <Icon className={cx('size-3.5', active && 'text-blue')} aria-hidden />
                {MODES[k].label}
              </button>
            )
          })}
        </div>
        <span className="hidden min-w-0 sm:block">
          <Badge tone="gray">{m.badge}</Badge>
        </span>
        <div className="ml-auto flex shrink-0 gap-1">
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
            <p>{m.intro}</p>
            <p className="mt-1 text-xs">{m.sub}</p>
            {EXAMPLE_GROUPS[mode].map((g) => (
              <div key={g.title} className="mt-3">
                <p className="text-xs font-bold text-navy">{g.title}</p>
                <div className="mt-1.5 flex flex-col gap-1.5">
                  {g.items.map((ex) => (
                    <button
                      key={ex.label}
                      type="button"
                      onClick={() => void send(ex.text)}
                      disabled={busy || API_URL === null}
                      title={ex.text}
                      className="rounded-control border border-border px-3 py-2 text-left text-xs text-text hover:bg-bg disabled:text-muted"
                    >
                      <span className="font-medium text-blue">{ex.label}</span>
                      <span className="mt-0.5 line-clamp-2 block text-muted">{ex.text}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
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
              onPolicy={mode === 'recommend' && !busy && API_URL !== null ? askPolicyFor : undefined}
            />
          ),
        )}
        {busy && <Loading message={m.loading} className="self-start text-left" />}
      </div>

      <form onSubmit={onSubmit} className="flex gap-2 border-t border-border p-3">
        <label className="sr-only" htmlFor="agent-input">
          메시지
        </label>
        <input
          id="agent-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={m.placeholder}
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
  onPolicy,
}: {
  turn: Turn
  addresses: Map<string, string>
  onAnswer?: (answer: string) => void // 마지막 확인 질문에만 (네 / 아니요 버튼)
  onPolicy?: (t: AgentTopItem) => void // TOP 매물 → 정책 안내 모드로
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
      {res?.recommendation_results && (
        <ResultCard rec={res.recommendation_results} whatIf={res.intent === 'what_if'} onPolicy={onPolicy} />
      )}
      {res?.comparison_results && <ComparisonCard cmp={res.comparison_results} addresses={addresses} />}
      {res?.analysis_results?.result && <AnalysisCard an={res.analysis_results} addresses={addresses} />}
      {res?.policy_results && <PolicyCard pol={res.policy_results} />}
    </div>
  )
}

// 주소의 시·구만 남긴다 (예: '서울특별시 서대문구 대현동 …' → '서울특별시 서대문구'). 정책 지역 검색용.
function regionOf(address: string | null): string | null {
  const parts = (address ?? '').trim().split(/\s+/)
  const gu = parts.findIndex((w) => /[구군시]$/.test(w) && !/특별시$|광역시$/.test(w))
  return gu > 0 ? parts.slice(0, gu + 1).join(' ') : null
}

// 정책 안내 모드로 보낼 질문. 매물의 사실(지역·형태·보증금·유지비)만 담고 자격 판단은 묻지 않는다.
function policyQuestion(t: AgentTopItem): string {
  const where = regionOf(t.address)
  const facts = [
    where && `${where}에 있는`,
    t.property_type && `${t.property_type}`,
    `매물(보증금 ${formatManwon(t.deposit_manwon)}, 월 유지비 ${formatManwon(t.monthly_cost_manwon)})`,
  ]
    .filter(Boolean)
    .join(' ')
  return `${facts}을 계약하려고 해. 이런 집을 구할 때 참고할 수 있는 주거 지원 정책을 공식 정보로 알려줘.`
}

function ResultCard({
  rec,
  whatIf,
  onPolicy,
}: {
  rec: AgentRecSummary
  whatIf: boolean
  onPolicy?: (t: AgentTopItem) => void
}) {
  return (
    <div className="rounded-card border border-border p-3">
      <p className="text-xs text-muted">
        {whatIf ? '가정한 조건' : '지금 조건'} · 후보 {rec.total_ranked}개 ·{' '}
        {rec.priorities.map((k, i) => `${i + 1}순위 ${label(k)} ${Math.round((rec.weights[k] ?? 0) * 100)}%`).join(', ')}
      </p>
      {rec.frequent && (
        <p className="mt-1 text-xs text-muted">
          자주 가는 곳:{' '}
          {rec.frequent.places.map((p) => `${p.place_name} (주 ${p.visits_per_week}회)`).join(', ')} · 상위{' '}
          {rec.frequent.top_n}개만 도보 계산{rec.frequent.unknown > 0 && ` · ${rec.frequent.unknown}개 미확인`}
        </p>
      )}
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
              {t.frequent_walk_min && Object.keys(t.frequent_walk_min).length > 0 && (
                <p className="text-xs text-muted">
                  {Object.entries(t.frequent_walk_min)
                    .map(([name, m]) => `${name}까지 도보 ${m === null ? '미확인' : `${m}분`}`)
                    .join(' · ')}
                </p>
              )}
              {t.same_house_ids.length > 0 && (
                <p className="text-xs text-muted">같은 집일 수 있는 광고 {t.same_house_ids.length}건 더 (묶어서 하나만 보여 줌)</p>
              )}
              {onPolicy && !whatIf && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                  <button type="button" onClick={() => onPolicy(t)} className="text-blue underline hover:text-navy">
                    관련 정책 보기
                  </button>
                  <Link to={`/properties/${encodeURIComponent(t.property_id)}`} className="text-blue underline hover:text-navy">
                    계약 전 확인
                  </Link>
                </div>
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
  excluded: '추천에서 빠진 매물과 이유',
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
  } else if (an.kind === 'excluded') {
    const x = r as unknown as ExcludedAnalysis
    body = (
      <>
        <p className="text-xs text-muted">
          전체 {x.total}개 · 통과 {x.ranked}개 · 빠짐 {x.excluded_count}개
        </p>
        <ul className="mt-1.5 flex flex-col gap-1.5 text-xs">
          {x.stages.map((s) => (
            <li key={s.stage}>
              <span className="font-medium">
                {s.label} {s.count}개
              </span>
              {Object.keys(s.by_condition).length > 0 && (
                <span className="text-muted">
                  {' '}
                  ({Object.entries(s.by_condition)
                    .map(([c, n]) => `${c} ${n}개`)
                    .join(', ')})
                </span>
              )}
              <ul className="mt-0.5 flex flex-col gap-0.5 pl-2 text-muted">
                {s.examples.map((e) => (
                  <li key={e.property_id}>
                    · {e.address ?? e.property_id}: {e.reason}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
        {x.not_recommended.length > 0 && (
          <>
            <p className="mt-2 text-xs font-medium">조건은 통과했지만 지금 우선순위에서 밀린 매물</p>
            <ul className="mt-0.5 flex flex-col gap-0.5 text-xs">
              {x.not_recommended.map((n) => (
                <li key={n.property_id}>
                  <Link to={`/properties/${encodeURIComponent(n.property_id)}`} className="text-blue underline">
                    {n.address ?? n.property_id}
                  </Link>{' '}
                  <span className="text-muted">(다른 우선순위 {n.best_rank_elsewhere}위)</span> — {n.message}
                </li>
              ))}
            </ul>
          </>
        )}
      </>
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

const POLICY_TITLE: Record<string, string> = {
  info: '주거 지원 정책 안내',
  eligibility: '주거 지원 정책 안내',
  compare: '정책 나란히 보기',
  apply: '신청 방법 안내',
  contract_safety: '계약 안전 점검 목록',
}

function PolicyCard({ pol }: { pol: PolicyResult }) {
  const kind = pol.kind ?? 'info'
  if (kind === 'contract_safety') {
    return (
      <div className="rounded-card border border-border p-3 text-sm">
        <div className="mb-1.5 flex items-center gap-1.5">
          <span className="text-xs font-bold text-navy">{POLICY_TITLE[kind]}</span>
          <Badge tone="gray">일반 안내 · 법률 자문 아님</Badge>
        </div>
        {(pol.checklist ?? []).map((sec) => (
          <div key={sec.phase} className="mt-1.5">
            <p className="text-xs font-medium text-text">{sec.phase}</p>
            <ul className="mt-0.5 flex flex-col gap-0.5 text-xs text-muted">
              {sec.items.map((it) => (
                <li key={it.text}>
                  ☐ {it.text}
                  {it.link && (
                    <>
                      {' '}
                      <a href={it.link.url} target="_blank" rel="noopener noreferrer" className="text-blue underline">
                        {it.link.name}
                      </a>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {(pol.notes ?? []).map((n) => (
          <p key={n} className="mt-2 text-[11px] text-muted">
            {n}
          </p>
        ))}
        <p className="mt-1 text-[11px] text-muted">{pol.checklist_source ?? pol.disclaimer}</p>
      </div>
    )
  }
  return (
    <div className="rounded-card border border-border p-3 text-sm">
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="text-xs font-bold text-navy">{POLICY_TITLE[kind] ?? POLICY_TITLE.info}</span>
        <Badge tone="gray">자격 판정 아님</Badge>
        {kind === 'compare' && <Badge tone="gray">유불리 판단 안 함</Badge>}
      </div>
      {(pol.notes ?? []).map((n) => (
        <p key={n} className="mb-1.5 text-[11px] text-muted">
          {n}
        </p>
      ))}
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
                {it.status_note && <p className="mt-0.5 text-amber-700">참고: {it.status_note}</p>}
                {kind === 'eligibility' && it.income_text && (
                  <p className="mt-0.5 text-muted">소득 조건 (공고 표기): {it.income_text}</p>
                )}
                {kind === 'apply' && (
                  <dl className="mt-0.5 text-muted">
                    {(
                      [
                        ['신청 방법', it.apply_method],
                        ['제출 서류', it.documents],
                        ['심사·발표', it.screening],
                      ] as const
                    ).map(([k, v]) => (
                      <div key={k}>
                        <dt className="inline font-medium">{k}: </dt>
                        <dd className="inline">{v ?? '공식 데이터에 표기 없음 → 공식 페이지 확인'}</dd>
                      </div>
                    ))}
                  </dl>
                )}
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
