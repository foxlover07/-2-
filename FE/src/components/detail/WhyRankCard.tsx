import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { CircleAlert, Trophy } from 'lucide-react'
import { factorLabel, type Factor } from '../../constants/recommend'
import { useProfile } from '../../contexts/ProfileContext'
import { useUniversity } from '../../contexts/UniversityContext'
import { API_URL, ApiError } from '../../services/api'
import { recommend, toRecommendRequest } from '../../services/recommendService'
import type { RankedProperty, RecommendResult } from '../../types/recommend'
import { cx } from '../../utils/cx'
import { missingForRecommend } from '../../utils/profile'
import { vsText } from '../../utils/vsTarget'
import { Badge, Card, Loading } from '../common'

// "이 매물은 왜 n위인가" (Phase 3 XAI). 매물 비교 화면과 같은 요청으로 엔진(POST /recommend)을 부르고,
// 엔진이 계산한 값(요소별 점수·가중치·기여 점수·기준 대비 차이·제외 사유)만으로 문장을 만든다. LLM 을 쓰지 않는다.

const STAGE_TEXT: Record<string, string> = {
  budget: '유지비 상한(+여유)을 넘어 추천에서 빠졌습니다',
  deposit: '보증금 상한(+여유)을 넘어 추천에서 빠졌습니다',
  exclusion: '장소 조건(AI 상담에서 추가)으로 추천에서 빠졌습니다',
  missing: '필요한 정보가 없어 추천에서 빠졌습니다',
  duplicate: '같은 매물 ID 가 중복되어 빠졌습니다',
}

type Load = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'done'; result: RecommendResult }

const pt = (v: number) => v.toLocaleString('ko-KR', { maximumFractionDigits: 1 })

export function WhyRankCard({ propertyId }: { propertyId: string }) {
  const { profile, isSaved } = useProfile()
  const { university } = useUniversity()
  const ready = API_URL !== null && isSaved && missingForRecommend(profile).length === 0
  const key = ready ? JSON.stringify(toRecommendRequest(profile, university)) : null
  const [res, setRes] = useState<{ key: string; load: Load } | null>(null)
  const load: Load = res && res.key === key ? res.load : { kind: 'loading' }

  useEffect(() => {
    if (key === null) return
    const ctrl = new AbortController()
    recommend(JSON.parse(key), ctrl.signal)
      .then((result) => setRes({ key, load: { kind: 'done', result } }))
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return
        setRes({ key, load: { kind: 'error', message: e instanceof ApiError ? e.message : '추천을 불러오지 못했습니다.' } })
      })
    return () => ctrl.abort()
  }, [key])

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Trophy className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">내 조건에서 이 매물의 순위</h2>
      </div>
      {!ready ? (
        <p className="mt-3 text-sm text-muted">
          {API_URL === null ? '서버에 연결되어 있을 때만 볼 수 있습니다.' : (
            <>
              <Link to="/profile" className="font-medium text-blue underline">
                생활 프로필
              </Link>
              을 저장하면 이 매물이 내 조건에서 몇 위인지, 왜 그런지 알려 드려요.
            </>
          )}
        </p>
      ) : load.kind === 'loading' ? (
        <Loading message="순위를 계산하는 중입니다…" className="py-6" />
      ) : load.kind === 'error' ? (
        <p className="mt-3 text-sm text-muted">순위를 불러오지 못했습니다. ({load.message})</p>
      ) : (
        <WhyBody result={load.result} propertyId={propertyId} />
      )}
    </Card>
  )
}

function WhyBody({ result, propertyId }: { result: RecommendResult; propertyId: string }) {
  const item = result.ranked.find((r) => r.property_id === propertyId)
  if (!item) {
    const ex = result.excluded.find((e) => e.property_id === propertyId)
    return (
      <div className="mt-3 flex gap-2 rounded-control bg-amber-50 px-3 py-2.5 text-sm text-amber">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        <p className="break-keep">
          {ex ? `${STAGE_TEXT[ex.stage] ?? '추천에서 빠졌습니다'}: ${ex.reason}` : '지금 선택한 대학의 추천 대상 매물이 아닙니다.'}
        </p>
      </div>
    )
  }
  const top1 = result.ranked[0]
  const order = result.priorities as Factor[]
  const contrib = (r: RankedProperty, f: Factor) => r.contributions[f] ?? 0
  // 강점·약점은 '내 기준(50점)보다 나은지'로 정한다 (기여 점수는 비중이 커서 약한 요소도 커 보일 수 있음)
  const fs = (f: Factor) => item.factor_scores[f] ?? 50
  const strong = [...order].filter((f) => fs(f) > 50).sort((a, b) => fs(b) - fs(a))
  const weak = [...order].filter((f) => fs(f) < 50).sort((a, b) => fs(a) - fs(b))
  const notRec = result.not_recommended.find((n) => n.property_id === propertyId)

  return (
    <>
      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-2xl font-bold text-blue">
          {item.rank}위 <span className="text-sm font-medium text-muted">/ {result.ranked.length}개</span>
        </p>
        <p className="text-sm text-text">
          종합 <b>{pt(item.score)}점</b>
          {item.rank > 1 && <span className="text-muted"> · 1위 {pt(top1.score)}점</span>}
        </p>
        {item.over_budget && <Badge tone="amber">예산 초과 (여유 구간)</Badge>}
      </div>

      <p className="mt-2 text-sm break-keep text-text">
        {strong.length > 0 ? (
          <>
            강점은 <b>{factorLabel(strong[0])}</b>({vsText(strong[0], item).text})
          </>
        ) : (
          '내 기준보다 나은 요소는 없고'
        )}
        {weak.length > 0 ? (
          <>
            {strong.length > 0 ? '이고, ' : ', '}가장 아쉬운 것은 <b>{factorLabel(weak[0])}</b>({vsText(weak[0], item).text})입니다.
          </>
        ) : (
          <>{strong.length > 0 ? '이고, 내 기준보다 못한 요소는 없습니다.' : ' 모든 요소가 내 기준과 같습니다.'}</>
        )}
      </p>

      <table className="mt-3 w-full text-xs">
        <thead>
          <tr className="text-left text-muted">
            <th className="py-1.5 font-normal">요소 (비중)</th>
            <th className="py-1.5 font-normal">내 기준 대비</th>
            <th className="py-1.5 text-right font-normal">요소 점수</th>
            <th className="py-1.5 text-right font-normal">기여</th>
            {item.rank > 1 && <th className="py-1.5 text-right font-normal">1위와 차이</th>}
          </tr>
        </thead>
        <tbody>
          {order.map((f, i) => {
            const v = vsText(f, item)
            const gap = contrib(item, f) - contrib(top1, f)
            return (
              <tr key={f} className="border-t border-border">
                <td className="py-1.5">
                  {i + 1}순위 {factorLabel(f)} <span className="text-muted">({Math.round((result.weights[f] ?? 0) * 100)}%)</span>
                </td>
                <td className={cx('py-1.5', v.good === true && 'text-green', v.good === false && 'text-amber')}>{v.text}</td>
                <td className="py-1.5 text-right">{item.factor_scores[f] === null ? '-' : pt(item.factor_scores[f] as number)}</td>
                <td className="py-1.5 text-right font-medium">{pt(contrib(item, f))}점</td>
                {item.rank > 1 && (
                  <td className={cx('py-1.5 text-right', gap >= 0 ? 'text-green' : 'text-muted')}>
                    {gap >= 0 ? '+' : '−'}
                    {pt(Math.abs(gap))}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>

      {notRec && (
        <p className="mt-3 rounded-control bg-blue-50 px-3 py-2 text-xs break-keep text-text">
          💡 {notRec.message}
        </p>
      )}
      <p className="mt-3 text-xs text-muted">
        요소 점수는 내 기준에서 50점, 좋은 쪽 단위당 +5점 · 나쁜 쪽 −10점 (단위: 편도 10분 = 월 5만 원 = 2㎡). 기여 = 비중 × 요소 점수.
        매물 비교 화면과 같은 조건으로 계산했습니다.
      </p>
    </>
  )
}
