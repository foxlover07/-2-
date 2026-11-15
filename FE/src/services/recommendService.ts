import { DEPARTMENT_BUILDINGS, type Factor } from '../constants/recommend'
import { DEFAULT_UNIVERSITY } from '../constants/universities'
import type { RecommendRequest, RecommendResult } from '../types/recommend'
import type { Profile } from '../utils/profile'
import { apiFetch } from './api'

// 추천 계산은 BE(POST /recommend, 추천 시스템 통합안)만 한다. FE 는 점수를 만들지 않는다.

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x))
}

/** 저장된 프로필 → 추천 요청. 필수 입력이 빠졌으면 호출 전에 missingForRecommend 로 막는다. */
export function toRecommendRequest(p: Profile, university: string = DEFAULT_UNIVERSITY): RecommendRequest {
  const dept = p.department.trim()
  // 학과 → 건물 예시표는 이대 것뿐이다. 다른 대학은 고른 건물을 그대로 보낸다.
  const preset = university === DEFAULT_UNIVERSITY ? DEPARTMENT_BUILDINGS[dept] : undefined
  return {
    university,
    department: dept || '직접 선택', // 학과 칸은 없앴다 (BE 는 표시용으로만 받는다)
    // 예시표와 같으면 null 로 보내 BE 가 예시표를 쓰게 한다 (assumptions 에 출처가 남는다)
    department_buildings: preset && sameSet(preset, p.departmentBuildings) ? null : p.departmentBuildings,
    grade: p.grade ?? 1,
    priorities: [p.priorities[0], p.priorities[1]] as [Factor, Factor],
    budget_manwon: p.budgetManwon ?? 0,
    deposit_budget_manwon: p.depositBudgetManwon,
    desired_commute_min: p.desiredCommuteMin ?? 0,
    desired_area_m2: p.desiredAreaM2 ?? 0,
    larger_area: p.largerArea,
    timetable: p.timetable,
    exclusions: p.exclusions, // 챗봇에서 승인한 장소 조건 (상가정보로 확인)
  }
}

export interface BuildingCoverage {
  name: string
  covered: number // 도보시간이 있는 매물 수
  total: number
  can_compute?: boolean // 건물 기준표에 좌표가 있어, 고르면 빈 매물을 그때 계산할 수 있는지
}

/** 그 대학에서 시간표에 고를 수 있는 건물 (BE GET /recommend/buildings). 도보시간이 있는 매물이 많은 순. */
export function getBuildings(university: string, signal?: AbortSignal): Promise<BuildingCoverage[]> {
  return apiFetch<BuildingCoverage[]>(`/recommend/buildings?university=${encodeURIComponent(university)}`, { signal })
}

export function recommend(req: RecommendRequest, signal?: AbortSignal): Promise<RecommendResult> {
  return apiFetch<RecommendResult>('/recommend', { method: 'POST', body: JSON.stringify(req), signal })
}

// ---------------------------------------------------------------- 시간표 강의동 → 도보시간 (BE POST /recommend/building-walks)

export interface BuildingWalkStat {
  input: string // 요청한 이름 그대로 (줄임말 가능)
  name: string | null // 표준 이름. 모르는 건물이면 null
  can_compute: boolean // 좌표가 있어 빈 매물을 계산할 수 있는지
  covered: number // 도보시간이 있는 매물 수
  total: number
  min_walk_min: number | null
  median_walk_min: number | null
}

export interface BuildingWalksResponse {
  buildings: BuildingWalkStat[]
  walks: Record<string, Record<string, number | null>> // 매물 ID → 표준 건물 이름 → 편도 도보(분)
  computed: number
  notes: string[]
}

export function getBuildingWalks(
  req: {
    university: string
    buildings: string[]
    property_ids?: string[]
    compute?: boolean
    budget_manwon?: number | null
    deposit_budget_manwon?: number | null
  },
  signal?: AbortSignal,
): Promise<BuildingWalksResponse> {
  return apiFetch<BuildingWalksResponse>('/recommend/building-walks', {
    method: 'POST',
    body: JSON.stringify(req),
    signal,
  })
}
