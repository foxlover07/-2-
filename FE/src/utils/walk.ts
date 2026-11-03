import type { Property } from '../types/property'

export interface Spot {
  name: string
  walkMin: number | null
}

// 강의동별 주간 방문 횟수. 키는 강의동 이름(spotN_name 값).
export type SpotVisits = Record<string, number>

// spot1~4를 이름·도보시간 쌍으로 묶는다. 이름이 없는 칸은 건너뛴다.
export function getSpots(p: Property): Spot[] {
  const pairs: Array<[string | null, number | null]> = [
    [p.spot1_name, p.spot1_walk_min],
    [p.spot2_name, p.spot2_walk_min],
    [p.spot3_name, p.spot3_walk_min],
    [p.spot4_name, p.spot4_walk_min],
  ]
  return pairs.flatMap(([name, walkMin]) => (name === null ? [] : [{ name, walkMin }]))
}

// 강의동은 spot 순서가 아니라 이름으로 찾는다.
export function getWalkMin(p: Property, spotName: string): number | null {
  return getSpots(p).find((s) => s.name === spotName)?.walkMin ?? null
}

// 데이터에 나오는 강의동 이름 목록(처음 나온 순서).
export function getSpotNames(list: Property[]): string[] {
  return [...new Set(list.flatMap((p) => getSpots(p).map((s) => s.name)))]
}

// 가중 평균 도보시간 = Σ(도보시간 × 주간 방문 횟수) ÷ Σ(방문 횟수), 소수 첫째 자리.
// 방문 0회인 강의동은 제외한다. 방문 합이 0이거나, 방문하는 강의동의
// 도보시간이 하나라도 없으면 계산할 수 없으므로 null.
export function getWeightedAvgWalk(p: Property, visits: SpotVisits): number | null {
  let weighted = 0
  let totalVisits = 0
  for (const [name, count] of Object.entries(visits)) {
    if (count <= 0) continue
    const walk = getWalkMin(p, name)
    if (walk === null) return null
    weighted += walk * count
    totalVisits += count
  }
  if (totalVisits === 0) return null
  return Math.round((weighted / totalVisits) * 10) / 10
}

// 가중 평균 도보시간이 가장 짧은 매물. 계산할 수 있는 매물이 없으면 null.
export function findShortestWalk(list: Property[], visits: SpotVisits): Property | null {
  let best: Property | null = null
  let bestWalk = Infinity
  for (const p of list) {
    const walk = getWeightedAvgWalk(p, visits)
    if (walk !== null && walk < bestWalk) {
      best = p
      bestWalk = walk
    }
  }
  return best
}
