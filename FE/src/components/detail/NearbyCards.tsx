import { useEffect, useState, type ReactNode } from 'react'
import { Building2, ExternalLink, FileText, Info, ListChecks, Receipt, Store } from 'lucide-react'
import { Badge, Card, Loading } from '../common'
import { ApiError } from '../../services/api'
import {
  getAmenities,
  getBuilding,
  getContractCheck,
  getMarketCompare,
  getRelatedPolicies,
} from '../../services/marketService'
import type {
  Amenities,
  AmenityCategory,
  BuildingInfo,
  ContractCheck,
  MarketCompare,
  RelatedPolicies,
} from '../../types/market'
import { cx } from '../../utils/cx'
import { formatManwon } from '../../utils/format'

// 매물 상세의 참고 정보 카드 3개 (Phase 7). 추천 점수·순위에는 쓰지 않는다.
// - 건축물대장: 광고 지번 기준 표제부 (사용승인일·층수·승강기·주차·주용도).
// - 주변 전월세 실거래 비교: 과거 계약 기록이라 현재 광고 매물과 섞지 않고 '참고'로만 보여 준다.
// - 생활 편의시설: 직선거리(m)와 반경 안 업소 수. 도보 경로 시간이 아니다.

type Load<T> = { kind: 'loading' } | { kind: 'no_server' } | { kind: 'error'; message: string } | { kind: 'ready'; data: T }

function useLoad<T>(id: string, fetcher: (id: string) => Promise<T | null>): Load<T> {
  const [state, setState] = useState<{ id: string; load: Load<T> } | null>(null)
  useEffect(() => {
    let cancelled = false
    fetcher(id)
      .then((data) => !cancelled && setState({ id, load: data === null ? { kind: 'no_server' } : { kind: 'ready', data } }))
      .catch((e) => {
        if (cancelled) return
        setState({ id, load: { kind: 'error', message: e instanceof ApiError ? e.message : '불러오지 못했습니다.' } })
      })
    return () => {
      cancelled = true
    }
  }, [id, fetcher])
  return state?.id === id ? state.load : { kind: 'loading' }
}

function CardHeader({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-2">
      {icon}
      <h2 className="text-base font-bold text-navy">{title}</h2>
      <Badge tone="gray" className="ml-auto">
        참고 정보
      </Badge>
    </div>
  )
}

function Status<T>({ load, label }: { load: Load<T>; label: string }) {
  if (load.kind === 'loading') return <Loading message={`${label}를 불러오는 중입니다…`} className="py-6" />
  if (load.kind === 'no_server') return <p className="mt-3 text-sm text-muted">서버에 연결되어 있을 때만 볼 수 있습니다.</p>
  if (load.kind === 'error') return <p className="mt-3 text-sm text-muted">{label}를 불러오지 못했습니다. ({load.message})</p>
  return null
}

function Notes({ notes, source, extra }: { notes: string[]; source: string; extra?: string }) {
  return (
    <div className="mt-4 border-t border-border pt-3">
      <ul className="flex flex-col gap-1">
        {notes.map((n) => (
          <li key={n} className="flex gap-1.5 text-xs break-keep text-muted">
            <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
            {n}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted">
        출처: {source}
        {extra && ` · ${extra}`}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------- 계약 전 확인
// 일반 점검 목록 + 서비스가 이 매물에 대해 가진 사실(실거래 비교·건축물대장·보증금). 안전 여부를 판정하지 않는다.

export function ContractCheckCard({ propertyId }: { propertyId: string }) {
  const load = useLoad(propertyId, getContractCheck)
  return (
    <Card>
      <CardHeader icon={<ListChecks className="size-5 text-blue" aria-hidden />} title="계약 전 확인" />
      {load.kind !== 'ready' ? <Status load={load} label="계약 전 확인 목록" /> : <ContractCheckBody c={load.data} />}
    </Card>
  )
}

function ContractCheckBody({ c }: { c: ContractCheck }) {
  return (
    <>
      <div className="mt-3 flex flex-col gap-4">
        {c.phases.map((s) => (
          <section key={s.phase}>
            <h3 className="text-sm font-bold text-text">{s.phase}</h3>
            <ul className="mt-1.5 flex flex-col gap-2">
              {s.items.map((it) => (
                <li key={it.text} className="text-sm break-keep">
                  <p className="text-text">
                    {it.text}
                    {it.link && (
                      <>
                        {' '}
                        <a href={it.link.url} target="_blank" rel="noreferrer" className="text-xs text-blue underline">
                          {it.link.name}
                        </a>
                      </>
                    )}
                  </p>
                  {it.fact && (
                    <p className="mt-1 rounded-control bg-blue-50 px-2.5 py-1.5 text-xs text-navy">
                      <span className="font-bold">이 매물:</span> {it.fact}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <Notes notes={c.notes} source={c.source} />
    </>
  )
}

// ---------------------------------------------------------------- 관련 정책 안내
// 매물의 임대 형태(월세/전세)·지역과 관련된 공식 정책. 자격(대상자 여부)을 판단하지 않고, 지원 금액을 유지비에 넣지 않는다.

export function RelatedPoliciesCard({ propertyId }: { propertyId: string }) {
  const load = useLoad(propertyId, getRelatedPolicies)
  return (
    <Card>
      <CardHeader icon={<FileText className="size-5 text-blue" aria-hidden />} title="관련 정책 안내" />
      {load.kind !== 'ready' ? <Status load={load} label="관련 정책" /> : <RelatedPoliciesBody r={load.data} />}
    </Card>
  )
}

function RelatedPoliciesBody({ r }: { r: RelatedPolicies }) {
  const extra = r.fetched_at ? `기준 ${r.fetched_at.slice(0, 10)}` : undefined
  if (!r.available || r.items.length === 0) {
    return (
      <>
        <p className="mt-3 text-sm break-keep text-muted">
          {r.reason ?? `이 매물(${r.rent_kind ?? '임대 형태 미상'})과 관련된 정책을 정책 데이터에서 찾지 못했습니다.`}
        </p>
        <OfficialLinks links={r.official_links} />
        <Notes notes={[...r.notes, r.disclaimer]} source={r.source} extra={extra} />
      </>
    )
  }
  return (
    <>
      <p className="mt-2 text-xs text-muted">
        {r.rent_kind} 매물 · {r.regions.filter((x) => !/특별시|광역시/.test(x)).join(' ') || r.regions.join(' ')} 기준
      </p>
      <ul className="mt-3 flex flex-col gap-2.5">
        {r.items.map((it) => {
          const link = it.apply_url ?? it.ref_url
          return (
            <li key={it.policy_id} className="rounded-control border border-border px-3 py-2.5 text-sm break-keep">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium text-navy">{it.name}</span>
                {(it.housing_types ?? []).map((t) => (
                  <Badge key={t} tone="blue">
                    {t}
                  </Badge>
                ))}
                {it.region && <Badge tone="gray">{it.region}</Badge>}
              </div>
              {(it.support ?? it.summary) && (
                <p className="mt-1 line-clamp-3 text-xs text-text">{it.support ?? it.summary}</p>
              )}
              {it.apply_period && <p className="mt-0.5 text-xs text-muted">신청 기간: {it.apply_period}</p>}
              {it.status_note && <p className="mt-0.5 text-xs text-amber">참고: {it.status_note}</p>}
              <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs">
                {link && (
                  <a href={link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-blue underline">
                    공식 페이지 <ExternalLink className="size-3" aria-hidden />
                  </a>
                )}
                {(it.housing_evidence ?? []).length > 0 && (
                  <span className="text-muted">분류 근거: {(it.housing_evidence ?? []).join(', ')}</span>
                )}
              </div>
            </li>
          )
        })}
      </ul>
      <Notes notes={[...r.notes, r.disclaimer]} source={r.source} extra={extra} />
    </>
  )
}

function OfficialLinks({ links }: { links: Array<{ name: string; url: string }> }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {links.map((l) => (
        <li key={l.url}>
          <a href={l.url} target="_blank" rel="noreferrer" className="text-blue underline">
            {l.name}
          </a>
        </li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------- 실거래 비교

const TX_LABEL: Record<string, string> = { sh: '단독/다가구', rh: '연립다세대', offi: '오피스텔' }

function levelTone(level: string | null): 'green' | 'blue' | 'amber' | 'gray' {
  if (level === null) return 'gray'
  if (level.includes('낮은')) return 'green'
  if (level.includes('높은')) return 'amber'
  return 'blue'
}

// 실거래 월세 25~75% 구간(띠)과 중앙값(선), 이 매물 월세(점)를 한 줄에 그린다.
function RentRange({ m }: { m: MarketCompare }) {
  const { rent_p25: p25, rent_p75: p75, rent_median: med, listing_monthly_rent_manwon: me } = m
  if (p25 === null || p75 === null || med === null || me === null) return null
  const lo = Math.min(p25, me)
  const hi = Math.max(p75, me)
  const pad = Math.max((hi - lo) * 0.15, 2)
  const min = Math.max(0, lo - pad)
  const max = hi + pad
  const pos = (v: number) => `${((v - min) / (max - min)) * 100}%`
  return (
    <div className="mt-5" aria-hidden>
      <div className="relative h-8">
        <div className="absolute top-3.5 right-0 left-0 h-1 rounded-full bg-bg" />
        <div className="absolute top-2.5 h-3 rounded-full bg-blue/25" style={{ left: pos(p25), width: `calc(${pos(p75)} - ${pos(p25)})` }} />
        <div className="absolute top-1.5 h-5 w-0.5 bg-blue" style={{ left: pos(med) }} />
        <div
          className="absolute top-2 size-4 -translate-x-1/2 rounded-full border-2 border-white bg-navy shadow"
          style={{ left: pos(me) }}
        />
      </div>
      <div className="flex justify-between text-[11px] text-muted">
        <span>{formatManwon(Math.round(min))}</span>
        <span>{formatManwon(Math.round(max))}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted">
        <span className="flex items-center gap-1">
          <span className="inline-block size-2.5 rounded-full bg-navy" />이 매물
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2.5 w-0.5 bg-blue" />실거래 중앙값
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-4 rounded-full bg-blue/25" />가운데 50% 구간
        </span>
      </div>
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-control bg-bg px-3 py-2.5">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-0.5 text-sm font-bold text-text">{value}</p>
      {sub && <p className="text-[11px] text-muted">{sub}</p>}
    </div>
  )
}

export function MarketCompareCard({ propertyId }: { propertyId: string }) {
  const load = useLoad(propertyId, getMarketCompare)
  return (
    <Card>
      <CardHeader icon={<Receipt className="size-5 text-blue" aria-hidden />} title="주변 전월세 실거래와 비교" />
      {load.kind !== 'ready' ? <Status load={load} label="실거래 비교" /> : <MarketBody m={load.data} />}
    </Card>
  )
}

function MarketBody({ m }: { m: MarketCompare }) {
  if (!m.available) {
    return (
      <>
        <p className="mt-3 text-sm break-keep text-muted">{m.reason ?? '비교할 수 없습니다.'}</p>
        {m.notes.length > 0 && <Notes notes={m.notes} source={m.source} />}
      </>
    )
  }
  const area = m.criteria.area_m2
  const scopeText = m.scope === '동' ? `같은 동(${m.criteria.umd_nm ?? ''})` : '같은 구'
  const types = (m.criteria.types ?? []).map((t) => TX_LABEL[t] ?? t).join('·')
  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Badge tone={levelTone(m.level)}>{m.level ?? '가격 수준 판단 안 함 (표본 부족)'}</Badge>
        {m.listing_percentile !== null && (
          <span className="text-xs text-muted">비교 거래 중 {m.listing_percentile}%가 이 매물보다 월세가 낮음</span>
        )}
      </div>

      <RentRange m={m} />

      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label="이 매물 월세" value={formatManwon(m.listing_monthly_rent_manwon)} sub={`관리비 ${formatManwon(m.listing_maintenance_manwon)} 별도`} />
        <Stat
          label="실거래 월세 중앙값"
          value={formatManwon(m.rent_median)}
          sub={m.rent_p25 !== null && m.rent_p75 !== null ? `가운데 50%: ${m.rent_p25}~${m.rent_p75}만원` : undefined}
        />
        <Stat label="실거래 보증금 중앙값" value={formatManwon(m.deposit_median)} />
      </div>

      <p className="mt-3 text-xs break-keep text-muted">
        비교 기준: {scopeText} · {types} 월세 계약 {m.n.toLocaleString('ko-KR')}건 ·{' '}
        {area ? `면적 ${area[0]}~${area[1]}㎡ · ` : ''}
        {m.period.length === 2 ? `계약일 ${m.period[0]} ~ ${m.period[1]}` : ''}
      </p>

      {m.recent.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-text">최근 비교 거래</p>
          <div className="mt-1.5 overflow-x-auto">
            <table className="w-full min-w-[420px] text-xs">
              <thead>
                <tr className="text-left text-muted">
                  <th className="py-1.5 font-normal">계약일</th>
                  <th className="py-1.5 font-normal">동</th>
                  <th className="py-1.5 font-normal">유형</th>
                  <th className="py-1.5 text-right font-normal">면적</th>
                  <th className="py-1.5 text-right font-normal">보증금</th>
                  <th className="py-1.5 text-right font-normal">월세</th>
                </tr>
              </thead>
              <tbody>
                {m.recent.map((r, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1.5">{r.contract_date}</td>
                    <td className="py-1.5">{r.umd_nm}</td>
                    <td className="py-1.5">{TX_LABEL[r.tx_type] ?? r.tx_type}</td>
                    <td className="py-1.5 text-right">{r.area_m2}㎡</td>
                    <td className="py-1.5 text-right">{formatManwon(r.deposit_manwon)}</td>
                    <td className="py-1.5 text-right font-medium">{formatManwon(r.monthly_rent_manwon)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Notes notes={m.notes} source={m.source} extra={m.fetched_at ? `조회 ${m.fetched_at.slice(0, 10)}` : undefined} />
    </>
  )
}

// ---------------------------------------------------------------- 생활 편의시설

const GROUP_TITLE: Record<AmenityCategory['group'], string> = {
  daily: '생활',
  health: '의료',
  context: '주변 상권 (참고)',
}

function AmenityItem({ c, radii }: { c: AmenityCategory; radii: number[] }) {
  const maxR = Math.max(...radii)
  return (
    <li className="rounded-control border border-border px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-text">{c.label}</span>
        <span className={cx('text-sm font-bold', c.nearest ? 'text-blue' : 'text-muted')}>
          {c.nearest ? `${c.nearest.distance_m}m` : `${maxR}m 안 없음`}
        </span>
      </div>
      {c.nearest && <p className="mt-0.5 truncate text-[11px] text-muted">가장 가까운 곳: {c.nearest.name}</p>}
      <p className="mt-0.5 text-[11px] text-muted">{radii.map((r) => `${r}m 안 ${c.counts[String(r)] ?? 0}곳`).join(' · ')}</p>
    </li>
  )
}

export function AmenitiesCard({ propertyId }: { propertyId: string }) {
  const load = useLoad(propertyId, getAmenities)
  return (
    <Card>
      <CardHeader icon={<Store className="size-5 text-blue" aria-hidden />} title="주변 생활 편의시설" />
      {load.kind !== 'ready' ? <Status load={load} label="편의시설 정보" /> : <AmenitiesBody a={load.data} />}
    </Card>
  )
}

function AmenitiesBody({ a }: { a: Amenities }) {
  if (!a.available) return <p className="mt-3 text-sm break-keep text-muted">{a.reason ?? '정보가 없습니다.'}</p>
  const groups = (['daily', 'health', 'context'] as const).map((g) => ({ g, items: a.categories.filter((c) => c.group === g) }))
  return (
    <>
      <p className="mt-1 text-xs text-muted">매물 위치에서 직선거리 기준</p>
      {groups
        .filter(({ items }) => items.length > 0)
        .map(({ g, items }) => (
          <section key={g} className="mt-4">
            <h3 className="text-xs font-bold text-muted">{GROUP_TITLE[g]}</h3>
            <ul className="mt-1.5 grid gap-2 sm:grid-cols-2">
              {items.map((c) => (
                <AmenityItem key={c.category} c={c} radii={a.radii_m} />
              ))}
            </ul>
          </section>
        ))}
      <Notes notes={a.notes} source={a.source} extra={a.stdr_ym.length ? `기준 ${a.stdr_ym.join(', ')}` : undefined} />
    </>
  )
}

// ---------------------------------------------------------------- 건축물대장

function num(v: number | null, unit: string): string {
  return v === null ? '정보 없음' : `${v.toLocaleString('ko-KR')}${unit}`
}

export function BuildingCard({ propertyId }: { propertyId: string }) {
  const load = useLoad(propertyId, getBuilding)
  return (
    <Card>
      <CardHeader icon={<Building2 className="size-5 text-blue" aria-hidden />} title="건물 정보 (건축물대장)" />
      {load.kind !== 'ready' ? <Status load={load} label="건축물대장 정보" /> : <BuildingBody b={load.data} />}
    </Card>
  )
}

function BuildingBody({ b }: { b: BuildingInfo }) {
  if (!b.available) return <p className="mt-3 text-sm break-keep text-muted">{b.reason ?? '정보가 없습니다.'}</p>
  const age = b.use_approval_year === null ? null : new Date().getFullYear() - b.use_approval_year
  return (
    <>
      {b.building_name && <p className="mt-1 text-xs text-muted">대장상 건물 이름: {b.building_name}</p>}
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Stat label="사용승인일" value={b.use_approval_date ?? '정보 없음'} sub={age !== null ? `약 ${age}년 차` : undefined} />
        <Stat label="층수" value={num(b.floors_above, '층')} sub={b.floors_below ? `지하 ${b.floors_below}층` : undefined} />
        <Stat label="승강기" value={num(b.elevators, '대')} />
        <Stat label="주차" value={num(b.parking, '대')} sub="건물 전체" />
        <Stat label="주용도" value={b.main_purpose ?? '정보 없음'} />
        <Stat label="구조" value={b.structure ?? '정보 없음'} />
      </div>
      {b.detail_purpose && <p className="mt-2 text-xs break-keep text-muted">세부 용도: {b.detail_purpose}</p>}
      <Notes notes={b.notes} source={b.source} extra={b.fetched_at ? `조회 ${b.fetched_at.slice(0, 10)}` : undefined} />
    </>
  )
}
