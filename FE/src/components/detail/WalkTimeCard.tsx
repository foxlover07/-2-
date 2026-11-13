import { Link } from 'react-router'
import { Footprints } from 'lucide-react'
import { Badge, Card } from '../common'
import type { Property } from '../../types/property'
import { formatMinutes } from '../../utils/format'
import { getSpots, getWeightedAvgWalk, type SpotVisits } from '../../utils/walk'
import { cx } from '../../utils/cx'

interface WalkTimeCardProps {
  property: Property
  visits: SpotVisits
}

export function WalkTimeCard({ property, visits }: WalkTimeCardProps) {
  const spots = getSpots(property)
  const weighted = getWeightedAvgWalk(property, visits)
  const hasVisits = Object.values(visits).some((n) => n > 0)
  // 막대 길이는 이 매물의 가장 긴 도보시간을 100%로 둔다.
  const maxWalk = Math.max(0, ...spots.map((s) => s.walkMin ?? 0))

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Footprints className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">강의동까지 도보시간</h2>
      </div>

      <div className="mt-4 rounded-control bg-blue-50 px-4 py-3">
        <p className="text-xs text-muted">내 학과 건물 평균 도보시간</p>
        <p className="mt-0.5 text-2xl font-bold text-blue">{formatMinutes(weighted)}</p>
        {!hasVisits && (
          <p className="mt-1 text-xs text-muted">
            <Link to="/profile" className="font-medium text-blue underline">
              생활 프로필
            </Link>
            에서 학과 건물을 고르면 계산됩니다.
          </p>
        )}
      </div>

      {spots.length === 0 ? (
        <p className="mt-4 text-sm text-muted">강의동 도보시간 정보 없음</p>
      ) : (
        <ul className="mt-5 flex flex-col gap-3.5">
          {spots.map((s) => {
            const count = visits[s.name] ?? 0
            const width = s.walkMin === null || maxWalk === 0 ? 0 : (s.walkMin / maxWalk) * 100
            return (
              <li key={s.name}>
                <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                  <span className="flex items-center gap-1.5 font-medium text-text">
                    {s.name}
                    {count > 0 && <Badge tone="blue">학과 건물</Badge>}
                  </span>
                  <span className={cx('font-bold', s.walkMin === null ? 'text-muted' : 'text-text')}>
                    {formatMinutes(s.walkMin)}
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-bg">
                  <div
                    className={cx('h-full rounded-full', count > 0 ? 'bg-blue' : 'bg-blue/35')}
                    style={{ width: `${width}%` }}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
