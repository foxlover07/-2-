import { useEffect, useMemo, useState } from 'react'
import { Footprints } from 'lucide-react'
import { API_URL } from '../../services/api'
import { getBuildingWalks, type BuildingWalksResponse } from '../../services/recommendService'
import { formatMinutes } from '../../utils/format'
import { Button } from '../common'
import type { TimetableRow } from './profileForm'

interface TimetableBuildingsProps {
  university: string
  rows: TimetableRow[]
  budgetManwon?: number | null // 계산할 매물을 예산 안으로 줄인다 (없으면 학교 매물 전체)
  depositBudgetManwon?: number | null
}

// 시간표(직접 입력·사진 인식)에 나온 강의동 목록과, 매물에서 그 건물까지 도보시간 현황 (7번, 2026-11-16).
// 도보시간이 없는 매물은 [도보시간 계산]으로 카카오 도보 경로를 그때 구한다 (BE app/building_walks, 결과 저장).
export function TimetableBuildings({ university, rows, budgetManwon, depositBudgetManwon }: TimetableBuildingsProps) {
  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of rows) if (r.building) m.set(r.building, (m.get(r.building) ?? 0) + 1)
    return m
  }, [rows])
  const names = useMemo(() => [...counts.keys()], [counts])
  const key = `${university}|${names.join(',')}`
  const [resp, setResp] = useState<{ key: string; data: BuildingWalksResponse } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (API_URL === null || names.length === 0) return
    const ctrl = new AbortController()
    getBuildingWalks({ university, buildings: names }, ctrl.signal)
      .then((data) => setResp({ key, data }))
      .catch(() => {})
    return () => ctrl.abort()
  }, [key, university, names])

  if (names.length === 0) return null
  const data = resp?.key === key ? resp.data : null
  const missing = data?.buildings.some((b) => b.name !== null && b.can_compute && b.covered < b.total) ?? false

  const compute = () => {
    setBusy(true)
    setError(null)
    getBuildingWalks({
      university,
      buildings: names,
      compute: true,
      budget_manwon: budgetManwon ?? null,
      deposit_budget_manwon: depositBudgetManwon ?? null,
    })
      .then((d) => setResp({ key, data: d }))
      .catch(() => setError('도보시간 계산에 실패했습니다. 잠시 뒤 다시 시도해 주세요.'))
      .finally(() => setBusy(false))
  }

  return (
    <div className="mt-4 rounded-control border border-border p-3">
      <p className="flex items-center gap-1.5 text-sm font-medium text-text">
        <Footprints className="size-4 text-blue" aria-hidden />
        시간표 강의동 {names.length}곳 · 매물에서 걸어서
      </p>
      <ul className="mt-2 divide-y divide-border">
        {names.map((n) => {
          const s = data?.buildings.find((b) => b.input === n)
          return (
            <li key={n} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 py-2 text-sm">
              <span className="font-medium text-text">
                {n} <span className="text-xs font-normal text-muted">주 {counts.get(n)}회 수업</span>
              </span>
              <span className="text-xs text-muted">
                {!s
                  ? '불러오는 중'
                  : s.name === null
                    ? '위치를 모르는 건물 · 도보시간 계산 불가'
                    : s.covered === 0
                      ? '도보시간 없음 · 계산 필요'
                      : `가장 가까운 매물 ${formatMinutes(s.min_walk_min)} · 중앙값 ${formatMinutes(s.median_walk_min)} · 매물 ${s.covered}/${s.total}곳`}
              </span>
            </li>
          )
        })}
      </ul>
      {data?.notes.map((note) => (
        <p key={note} className="mt-1 text-xs text-muted">
          {note}
        </p>
      ))}
      {error && (
        <p role="alert" className="mt-1 text-xs text-red">
          {error}
        </p>
      )}
      {missing && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted">
            비어 있는 매물은 {budgetManwon ? `유지비 ${budgetManwon}만 원(+여유) 안` : '학교 매물 전체'}에서 카카오 도보 경로로
            계산합니다. 한 번에 최대 150곳, 하루 한도가 있습니다.
          </p>
          <Button variant="secondary" size="sm" onClick={compute} disabled={busy}>
            {busy ? '계산 중…' : '도보시간 계산'}
          </Button>
        </div>
      )}
    </div>
  )
}
