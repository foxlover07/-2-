import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { Footprints } from 'lucide-react'
import { Badge, Card } from '../common'
import { useProfile } from '../../contexts/ProfileContext'
import { API_URL } from '../../services/api'
import { getBuildingWalks, type BuildingWalksResponse } from '../../services/recommendService'
import type { Property } from '../../types/property'
import { formatMinutes } from '../../utils/format'
import { getSpots } from '../../utils/walk'
import { cx } from '../../utils/cx'

// 강의동까지 도보시간 (2026-11-16: 생활 프로필의 학과 건물·시간표 건물 기준으로 바꿈).
// 값은 BE 도보시간 표(POST /recommend/building-walks, 카카오 도보 경로 편도)에서 가져온다. 계산하지 않은 건물은 '정보 없음'.
// 생활 프로필에 건물이 없으면 매물 데이터의 기본 강의동(spot1~4)을 보여 준다.

interface Row {
  name: string
  walkMin: number | null
  dept: boolean
  timetableCount: number
  unknown: boolean // 데이터에 없는 건물
}

const round1 = (v: number) => Math.round(v * 10) / 10

export function WalkTimeCard({ property }: { property: Property }) {
  const { profile } = useProfile()
  const names = useMemo(
    () => [...new Set([...profile.departmentBuildings, ...profile.timetable.map((s) => s.building)])].filter(Boolean),
    [profile.departmentBuildings, profile.timetable],
  )
  const key = `${property.university}|${property.property_id}|${names.join(',')}`
  const [resp, setResp] = useState<{ key: string; data: BuildingWalksResponse | null } | null>(null)

  useEffect(() => {
    if (API_URL === null || names.length === 0) return
    const ctrl = new AbortController()
    getBuildingWalks({ university: property.university, buildings: names, property_ids: [property.property_id] }, ctrl.signal)
      .then((data) => setResp({ key, data }))
      .catch(() => {
        if (!ctrl.signal.aborted) setResp({ key, data: null })
      })
    return () => ctrl.abort()
  }, [key, names, property.university, property.property_id])

  const usesProfile = names.length > 0 && API_URL !== null
  const loading = usesProfile && resp?.key !== key
  const data = resp?.key === key ? resp.data : null

  const timetableCount = new Map<string, number>()
  for (const s of profile.timetable) timetableCount.set(s.building, (timetableCount.get(s.building) ?? 0) + 1)

  let rows: Row[]
  if (usesProfile && data) {
    const walks = data.walks[property.property_id] ?? {}
    rows = names.map((n) => {
      const stat = data.buildings.find((b) => b.input === n)
      const std = stat?.name ?? null
      return {
        name: std ?? n,
        walkMin: std ? (walks[std] ?? null) : null,
        dept: profile.departmentBuildings.includes(n),
        timetableCount: timetableCount.get(n) ?? 0,
        unknown: stat ? stat.name === null : false,
      }
    })
  } else {
    // 프로필 건물이 없거나 서버 응답이 없을 때: 매물 데이터의 기본 강의동
    rows = getSpots(property).map((s) => ({
      name: s.name,
      walkMin: s.walkMin,
      dept: profile.departmentBuildings.includes(s.name),
      timetableCount: timetableCount.get(s.name) ?? 0,
      unknown: false,
    }))
  }

  // 위 요약: 학과 건물 평균. 학과 건물이 없으면 시간표 수업 횟수로 가중 평균
  const deptVals = rows.filter((r) => r.dept && r.walkMin !== null).map((r) => r.walkMin as number)
  const ttRows = rows.filter((r) => r.timetableCount > 0 && r.walkMin !== null)
  const ttTotal = ttRows.reduce((a, r) => a + r.timetableCount, 0)
  const summary =
    deptVals.length > 0
      ? round1(deptVals.reduce((a, b) => a + b, 0) / deptVals.length)
      : ttTotal > 0
        ? round1(ttRows.reduce((a, r) => a + (r.walkMin as number) * r.timetableCount, 0) / ttTotal)
        : null
  const summaryLabel =
    deptVals.length > 0 || ttTotal === 0 ? '주로 가는 건물 평균 도보시간' : '내 시간표 건물 평균 도보시간 (수업 횟수 가중)'
  const maxWalk = Math.max(0, ...rows.map((r) => r.walkMin ?? 0))

  return (
    <Card>
      <div className="flex items-center gap-2">
        <Footprints className="size-5 text-blue" aria-hidden />
        <h2 className="text-base font-bold text-navy">강의동까지 도보시간</h2>
        {usesProfile && <Badge tone="blue">생활 프로필 기준</Badge>}
      </div>

      <div className="mt-4 rounded-control bg-blue-50 px-4 py-3">
        <p className="text-xs text-muted">{summaryLabel}</p>
        <p className="mt-0.5 text-2xl font-bold text-blue">{loading ? '…' : formatMinutes(summary)}</p>
        {!usesProfile && (
          <p className="mt-1 text-xs text-muted">
            <Link to="/profile" className="font-medium text-blue underline">
              생활 프로필
            </Link>
            에서 수업 듣는 건물이나 시간표를 넣으면 그 건물 기준으로 보여 줍니다.
          </p>
        )}
      </div>

      {loading ? (
        <p className="mt-4 text-sm text-muted">도보시간을 불러오는 중…</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 text-sm text-muted">강의동 도보시간 정보 없음</p>
      ) : (
        <ul className="mt-5 flex flex-col gap-3.5">
          {rows.map((r) => {
            const mine = r.dept || r.timetableCount > 0
            const width = r.walkMin === null || maxWalk === 0 ? 0 : (r.walkMin / maxWalk) * 100
            return (
              <li key={r.name}>
                <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                  <span className="flex flex-wrap items-center gap-1.5 font-medium text-text">
                    {r.name}
                    {r.dept && r.timetableCount === 0 && <Badge tone="blue">주로 가는 건물</Badge>}
                    {r.timetableCount > 0 && <Badge tone="gray">시간표 주 {r.timetableCount}회</Badge>}
                  </span>
                  <span
                    className={cx('shrink-0 font-bold', r.walkMin === null ? 'text-sm font-normal text-muted' : 'text-text')}
                  >
                    {r.unknown ? '데이터에 없는 건물' : formatMinutes(r.walkMin)}
                  </span>
                </div>
                <div className="h-2.5 overflow-hidden rounded-full bg-bg">
                  <div
                    className={cx('h-full rounded-full', mine ? 'bg-blue' : 'bg-blue/35')}
                    style={{ width: `${width}%` }}
                  />
                </div>
              </li>
            )
          })}
        </ul>
      )}
      <p className="mt-3 text-xs text-muted">
        카카오 도보 경로 기준 편도 (건물 대표 지점, 출입구 아님). 도보시간이 없는 건물은 &apos;정보 없음&apos;으로 두고 추측하지
        않습니다.
      </p>
    </Card>
  )
}
