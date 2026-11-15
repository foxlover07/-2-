import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { CircleAlert, Scale } from 'lucide-react'
import { useUniversity } from '../../contexts/UniversityContext'
import { API_URL, ApiError } from '../../services/api'
import { runAnalysis, type AnalysisResultMap } from '../../services/analysisService'
import { toRecommendRequest } from '../../services/recommendService'
import type { AnalysisTab, ParetoItem, ParetoResult, RelaxationResult, SensitivityResult, Summary, TradeoffResult } from '../../types/analysis'
import type { Property } from '../../types/property'
import { cx } from '../../utils/cx'
import { formatManwon } from '../../utils/format'
import { missingForRecommend, type Profile } from '../../utils/profile'
import { Badge, Card, Loading } from '../common'

// 의사결정 분석 (BE /analysis/*). 추천 결과 아래에서 추천과 같은 조건으로 계산한다.
// 모두 참고 분석이며 추천 점수·순위를 바꾸지 않는다. 탭을 고를 때 그 분석만 부른다.

const TABS: Array<{ key: AnalysisTab; label: string; hint: string }> = [
  { key: 'pareto', label: '손해 없는 후보', hint: '유지비·보증금·통학·면적 모두에서 다른 매물보다 못하지 않은 매물 (Pareto)' },
  { key: 'tradeoff', label: '돈 ↔ 통학', hint: '추천 1위 기준, 돈을 더 내면 통학이 얼마나 줄고 비슷한 통학에서 얼마나 아낄 수 있는지' },
  { key: 'sensitivity', label: '순위 안정성', hint: '우선순위·가중치·예산·기준을 조금 바꿨을 때 상위 3개가 얼마나 유지되는지' },
  { key: 'relaxation', label: '조건 완화', hint: '후보가 없거나 적을 때 어떤 조건을 얼마나 풀면 되는지 (가정)' },
]

type Load<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'done'; data: T }

interface Props {
  profile: Profile
  isSaved: boolean
  properties: Property[]
}

export function DecisionAnalysis({ profile, isSaved, properties }: Props) {
  const { university } = useUniversity()
  const [tab, setTab] = useState<AnalysisTab>('pareto')
  const ready = API_URL !== null && isSaved && missingForRecommend(profile).length === 0
  const requestKey = ready ? JSON.stringify(toRecommendRequest(profile, university)) : null
  const loadKey = requestKey === null ? null : `${tab}|${requestKey}`
  const [res, setRes] = useState<{ key: string; load: Load<unknown> } | null>(null)
  const load: Load<unknown> = res && res.key === loadKey ? res.load : { kind: 'loading' }
  const byId = useMemo(() => new Map(properties.map((p) => [p.property_id, p])), [properties])

  useEffect(() => {
    if (loadKey === null || requestKey === null) return
    const ctrl = new AbortController()
    runAnalysis(tab, JSON.parse(requestKey), ctrl.signal)
      .then((data) => setRes({ key: loadKey, load: { kind: 'done', data } }))
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return
        setRes({ key: loadKey, load: { kind: 'error', message: e instanceof ApiError ? e.message : '분석을 불러오지 못했습니다.' } })
      })
    return () => ctrl.abort()
  }, [loadKey, requestKey, tab])

  if (!ready) return null
  const name = (id: string | null | undefined) => (id ? (byId.get(id)?.address ?? id) : '')
  const info = TABS.find((t) => t.key === tab)!

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Scale className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">의사결정 분석</h2>
        <Badge tone="gray">참고 · 추천 순위는 그대로</Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-1 rounded-control bg-bg p-1" role="tablist" aria-label="분석 종류">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={cx(
              'rounded-[9px] px-3.5 py-1.5 text-sm',
              tab === t.key ? 'bg-white font-bold text-navy shadow-sm' : 'font-medium text-muted hover:text-text',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <p className="mt-2 text-xs break-keep text-muted">{info.hint}</p>

      <div className="mt-4" role="tabpanel">
        {load.kind === 'loading' ? (
          <Loading message="분석을 계산하는 중입니다…" className="py-8" />
        ) : load.kind === 'error' ? (
          <p className="text-sm text-muted">분석을 불러오지 못했습니다. ({load.message})</p>
        ) : tab === 'pareto' ? (
          <ParetoView r={load.data as ParetoResult} name={name} />
        ) : tab === 'tradeoff' ? (
          <TradeoffView r={load.data as AnalysisResultMap['tradeoff']} name={name} />
        ) : tab === 'sensitivity' ? (
          <SensitivityView r={load.data as SensitivityResult} name={name} />
        ) : (
          <RelaxationView r={load.data as RelaxationResult} name={name} />
        )}
      </div>
    </Card>
  )
}

type NameFn = (id: string | null | undefined) => string

function PropLink({ id, name, children }: { id: string; name: NameFn; children?: ReactNode }) {
  return (
    <Link to={`/properties/${encodeURIComponent(id)}`} className="font-medium text-navy hover:underline">
      {children ?? name(id)}
    </Link>
  )
}

function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null
  return (
    <ul className="mt-3 flex flex-col gap-1 text-xs text-muted">
      {notes.map((n) => (
        <li key={n} className="flex gap-1.5 break-keep">
          <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden />
          {n}
        </li>
      ))}
    </ul>
  )
}

const minText = (v: number | null) => (v === null ? '정보 없음' : `${v}분`)

// ---------------------------------------------------------------- Pareto: 유지비 × 통학 산점도 + 목록

function ParetoView({ r, name }: { r: ParetoResult; name: NameFn }) {
  const [hover, setHover] = useState<ParetoItem | null>(null)
  const topOff = r.top_k.filter((t) => !t.pareto)
  const pts = [...r.frontier, ...topOff].filter((p) => p.travel_min !== null)
  const W = 640
  const H = 260
  const M = { l: 44, r: 12, t: 12, b: 34 }
  const xs = pts.map((p) => p.monthly_cost_manwon)
  const ys = pts.map((p) => p.travel_min as number)
  const xMin = Math.max(0, Math.floor((Math.min(...xs) - 5) / 10) * 10)
  const xStep = Math.max(5, Math.ceil((Math.max(...xs) + 5 - xMin) / 4 / 5) * 5)
  const xMax = xMin + xStep * 4
  // 눈금은 5분 단위의 깔끔한 값 4칸
  const yStep = Math.max(5, Math.ceil(Math.max(...ys) / 4 / 5) * 5)
  const yMax = yStep * 4
  const sx = (v: number) => M.l + ((v - xMin) / Math.max(1, xMax - xMin)) * (W - M.l - M.r)
  const sy = (v: number) => H - M.b - (v / Math.max(1, yMax)) * (H - M.t - M.b)
  const xTicks = Array.from({ length: 5 }, (_, i) => xMin + xStep * i)
  const yTicks = Array.from({ length: 5 }, (_, i) => yStep * i)
  const topIds = new Set(r.top_k.map((t) => t.property_id))

  return (
    <>
      <p className="text-sm text-text">
        후보 <b>{r.candidates}개</b> 중 다른 매물보다 모든 면({r.objectives.map((o) => r.objective_labels[o] ?? o).join('·')})에서 못하지
        않은 매물은 <b className="text-blue">{r.frontier.length}개</b>입니다. 나머지 {r.dominated_count}개는 더 나은 대안이 있습니다.
      </p>
      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {r.top_k.map((t) => (
          <span key={t.property_id} className="rounded-md bg-bg px-2 py-1">
            추천 {t.rank}위 <PropLink id={t.property_id} name={name} /> —{' '}
            {t.pareto ? <b className="text-green">손해 없는 후보</b> : <span className="text-muted">{name(t.dominated_by)}보다 못함</span>}
          </span>
        ))}
      </div>

      {pts.length > 0 && (
        <div className="relative mt-4">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="손해 없는 후보의 월 유지비와 통학 시간">
            {yTicks.map((t) => (
              <g key={`y${t}`}>
                <line x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} stroke="var(--color-border)" strokeWidth={1} />
                <text x={M.l - 6} y={sy(t) + 4} textAnchor="end" fontSize={11} fill="var(--color-muted)">
                  {t}분
                </text>
              </g>
            ))}
            {xTicks.map((t) => (
              <text key={`x${t}`} x={sx(t)} y={H - M.b + 16} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
                {t}만
              </text>
            ))}
            <text x={W - M.r} y={H - 4} textAnchor="end" fontSize={11} fill="var(--color-muted)">
              월 유지비 →
            </text>
            {pts.map((p) => {
              const on = r.frontier.some((f) => f.property_id === p.property_id)
              return (
                <g key={p.property_id} onMouseEnter={() => setHover(p)} onMouseLeave={() => setHover(null)}>
                  <circle cx={sx(p.monthly_cost_manwon)} cy={sy(p.travel_min as number)} r={12} fill="transparent" />
                  <circle
                    cx={sx(p.monthly_cost_manwon)}
                    cy={sy(p.travel_min as number)}
                    r={topIds.has(p.property_id) ? 6 : 4.5}
                    fill={on ? 'var(--color-blue)' : 'var(--color-surface, #fff)'}
                    stroke={on ? '#fff' : 'var(--color-navy)'}
                    strokeWidth={2}
                  />
                </g>
              )
            })}
          </svg>
          {hover && (
            <div
              className="pointer-events-none absolute z-10 rounded-control border border-border bg-white px-3 py-2 text-xs shadow-md"
              style={{ left: `${(sx(hover.monthly_cost_manwon) / W) * 100}%`, top: `${(sy(hover.travel_min as number) / H) * 100}%`, transform: 'translate(-50%, calc(-100% - 10px))' }}
            >
              <p className="font-bold text-text">
                {hover.rank}위 · {name(hover.property_id)}
              </p>
              <p className="text-muted">
                유지비 {formatManwon(hover.monthly_cost_manwon)} · 통학 {minText(hover.travel_min)} · {hover.area_m2}㎡ · 보증금{' '}
                {formatManwon(hover.deposit_manwon)}
              </p>
            </div>
          )}
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <span className="inline-block size-2.5 rounded-full bg-blue" />손해 없는 후보
            </span>
            {topOff.length > 0 && (
              <span className="flex items-center gap-1.5">
                <span className="inline-block size-2.5 rounded-full border-2 border-navy bg-white" />
                추천 상위 중 손해 있는 매물
              </span>
            )}
            <span>큰 점 = 추천 상위 {r.top_k.length}개 · 점에 마우스를 올리면 자세히</span>
          </div>
        </div>
      )}

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs font-medium text-blue">손해 없는 후보 목록 ({r.frontier.length}개, 유지비 낮은 순)</summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[480px] text-xs">
            <thead>
              <tr className="text-left text-muted">
                <th className="py-1.5 font-normal">추천 순위</th>
                <th className="py-1.5 font-normal">매물</th>
                <th className="py-1.5 text-right font-normal">유지비</th>
                <th className="py-1.5 text-right font-normal">보증금</th>
                <th className="py-1.5 text-right font-normal">통학</th>
                <th className="py-1.5 text-right font-normal">면적</th>
              </tr>
            </thead>
            <tbody>
              {r.frontier.map((f) => (
                <tr key={f.property_id} className="border-t border-border">
                  <td className="py-1.5">{f.rank}위</td>
                  <td className="py-1.5">
                    <PropLink id={f.property_id} name={name} />
                  </td>
                  <td className="py-1.5 text-right">{formatManwon(f.monthly_cost_manwon)}</td>
                  <td className="py-1.5 text-right">{formatManwon(f.deposit_manwon)}</td>
                  <td className="py-1.5 text-right">{minText(f.travel_min)}</td>
                  <td className="py-1.5 text-right">{f.area_m2}㎡</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <Notes notes={r.notes} />
    </>
  )
}

// ---------------------------------------------------------------- Trade-off

function SummaryLine({ s, name }: { s: Summary; name: NameFn }) {
  return (
    <span>
      {s.rank}위 <PropLink id={s.property_id} name={name} />{' '}
      <span className="text-muted">
        (월 {formatManwon(s.monthly_cost_manwon)} · 통학 {minText(s.travel_min)} · {s.area_m2}㎡)
      </span>
    </span>
  )
}

function TradeoffView({ r, name }: { r: TradeoffResult | null; name: NameFn }) {
  if (r === null) return <p className="text-sm text-muted">조건을 통과한 매물이 없어 비교할 수 없습니다.</p>
  const steps = r.pay_more_for_commute.filter((s) => s.best && s.travel_saved_min && s.travel_saved_min > 0)
  return (
    <>
      <p className="text-sm">
        기준: <SummaryLine s={r.reference} name={name} />
      </p>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded-control border border-border p-3">
          <p className="text-xs font-bold text-navy">돈을 더 내면 통학이 얼마나 줄어드나</p>
          {steps.length === 0 ? (
            <p className="mt-1.5 text-xs text-muted">지금 조건 안에서는 돈을 더 내도 통학이 더 짧은 매물이 없습니다.</p>
          ) : (
            <ul className="mt-1.5 flex flex-col gap-1.5 text-xs">
              {steps.map((s) => (
                <li key={s.extra_budget_manwon}>
                  <b>월 +{s.extra_budget_manwon}만 원</b>까지 → 통학 <b className="text-blue">{s.travel_saved_min}분 단축</b>
                  <br />
                  <SummaryLine s={s.best!} name={name} />
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-control border border-border p-3">
          <p className="text-xs font-bold text-navy">아끼려면</p>
          <ul className="mt-1.5 flex flex-col gap-1.5 text-xs">
            {[r.similar_commute_cheaper, ...r.smaller_area_cheaper].map((s) => (
              <li key={s.label}>
                {s.label}:{' '}
                {s.best ? (
                  <>
                    <b className="text-green">월 {s.saved_manwon}만 원 절약</b>
                    <br />
                    <SummaryLine s={s.best} name={name} />
                  </>
                ) : (
                  <span className="text-muted">없음</span>
                )}
              </li>
            ))}
            {r.cheapest && (
              <li>
                가장 싼 후보: <SummaryLine s={r.cheapest} name={name} />
              </li>
            )}
          </ul>
        </div>
      </div>
      <Notes notes={r.notes} />
    </>
  )
}

// ---------------------------------------------------------------- 민감도

function SensitivityView({ r, name }: { r: SensitivityResult; name: NameFn }) {
  return (
    <>
      <p className="text-sm">
        조건을 조금씩 바꾼 {r.scenarios.length}가지 가정에서 지금 상위 {r.top_k}개가 얼마나 남는지입니다. 낮을수록 그 조건에 민감합니다.
      </p>
      <ul className="mt-3 flex flex-col gap-2.5">
        {r.by_kind.map((k) => {
          const v = k.mean_top_retention
          return (
            <li key={k.kind}>
              <div className="mb-1 flex justify-between gap-2 text-xs">
                <span className="font-medium text-text">
                  {k.label} <span className="text-muted">({k.scenarios}가지)</span>
                </span>
                <span className="font-bold text-text">{v === null ? '-' : `${Math.round(v * 100)}% 유지`}</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-bg">
                <div className="h-full rounded-full bg-blue" style={{ width: `${(v ?? 0) * 100}%` }} />
              </div>
            </li>
          )
        })}
      </ul>
      {r.stable.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-bold text-navy">자주 상위권에 드는 매물</p>
          <ul className="mt-1.5 flex flex-col gap-1 text-xs">
            {r.stable.slice(0, 5).map((s) => (
              <li key={s.property_id}>
                <PropLink id={s.property_id} name={name} />{' '}
                <span className="text-muted">
                  — {s.total}가지 중 {s.in_top}번 상위 {r.top_k}
                  {s.baseline_rank !== null && ` · 지금 ${s.baseline_rank}위`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Notes notes={r.notes} />
    </>
  )
}

// ---------------------------------------------------------------- 조건 완화

function RelaxationView({ r, name }: { r: RelaxationResult; name: NameFn }) {
  return (
    <>
      <p className="text-sm">
        지금 후보 <b>{r.baseline_candidates}개</b>
        {r.needs_relaxation
          ? ` — 목표(${r.target_count}개)보다 적습니다. 아래처럼 풀면 후보가 생깁니다 (가정이며 조건은 바뀌지 않습니다).`
          : ` — 목표(${r.target_count}개) 이상이라 조건을 풀 필요는 없습니다. 참고로 각 조건을 조금 풀었을 때입니다.`}
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {r.minimal.map((o) => (
          <li key={o.lever + o.label} className="rounded-control border border-border px-3 py-2 text-xs">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium text-text">{o.label}</span>
              <span>
                후보 <b>{o.candidates}개</b> <span className="text-green">(+{o.added})</span>
              </span>
            </div>
            {o.added_examples.length > 0 && (
              <p className="mt-1 text-muted">
                새로 들어오는 예: {o.added_examples.map((s) => name(s.property_id)).join(', ')}
              </p>
            )}
          </li>
        ))}
        {r.combined && (
          <li className="rounded-control border border-dashed border-border px-3 py-2 text-xs">
            두 가지 함께: <b>{r.combined.label}</b> → 후보 {r.combined.candidates}개
          </li>
        )}
      </ul>
      <Notes notes={r.notes} />
    </>
  )
}
