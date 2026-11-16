import { useEffect, useMemo, useState } from 'react'
import { MapPin } from 'lucide-react'
import { useProfile } from '../../contexts/ProfileContext'
import { API_URL } from '../../services/api'
import { getBuildingWalks, type BuildingWalksResponse } from '../../services/recommendService'
import { formatMinutes } from '../../utils/format'
import { Card } from '../common'

// 이 매물에서 내 수업 건물·시간표 건물까지 편도 도보시간 (BE 도보시간 표 + 그때 계산해 저장한 값).
// 매물 CSV 의 강의동 4개(spot1~4)만 보여 주는 '강의동까지 도보시간' 카드를 보완한다. 없는 값은 계산하지 않고 '정보 없음'.
export function MyBuildingsWalkCard({ propertyId, university }: { propertyId: string; university: string }) {
  const { profile } = useProfile()
  const names = useMemo(
    () => [...new Set([...profile.departmentBuildings, ...profile.timetable.map((s) => s.building)])],
    [profile.departmentBuildings, profile.timetable],
  )
  const key = `${university}|${propertyId}|${names.join(',')}`
  const [resp, setResp] = useState<{ key: string; data: BuildingWalksResponse } | null>(null)

  useEffect(() => {
    if (API_URL === null || names.length === 0) return
    const ctrl = new AbortController()
    getBuildingWalks({ university, buildings: names, property_ids: [propertyId] }, ctrl.signal)
      .then((data) => setResp({ key, data }))
      .catch(() => {})
    return () => ctrl.abort()
  }, [key, university, names, propertyId])

  if (names.length === 0 || API_URL === null) return null
  const data = resp?.key === key ? resp.data : null
  const walks = data?.walks[propertyId] ?? {}
  const days = new Map<string, number>()
  for (const s of profile.timetable) days.set(s.building, (days.get(s.building) ?? 0) + 1)

  return (
    <Card>
      <div className="flex items-center gap-2">
        <MapPin className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">내 수업 건물까지 도보시간</h2>
      </div>
      <ul className="mt-3 divide-y divide-border">
        {names.map((n) => {
          const stat = data?.buildings.find((b) => b.input === n)
          const std = stat?.name ?? null
          const min = std ? walks[std] ?? null : null
          return (
            <li key={n} className="flex items-baseline justify-between gap-3 py-2 text-sm">
              <span className="text-text">
                {n}
                {days.get(n) ? <span className="ml-1 text-xs text-muted">시간표 주 {days.get(n)}회</span> : null}
              </span>
              <span className={min === null ? 'text-xs text-muted' : 'font-medium text-navy'}>
                {!data ? '불러오는 중' : stat && stat.name === null ? '위치 모름' : min === null ? '아직 계산 안 됨' : formatMinutes(min)}
              </span>
            </li>
          )
        })}
      </ul>
      <p className="mt-2 text-xs text-muted">
        카카오 도보 경로(건물 대표 지점, 출입구 아님) 기준 편도. 계산 안 된 건물은 생활 프로필 시간표의 [도보시간 계산]이나 추천
        요청 때 채워집니다.
      </p>
    </Card>
  )
}
