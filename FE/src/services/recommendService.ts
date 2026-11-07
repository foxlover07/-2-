import { DEPARTMENT_BUILDINGS } from '../constants/recommend'
import type { RecommendRequest, RecommendResult } from '../types/recommend'
import type { Profile } from '../utils/profile'
import { apiFetch } from './api'

// 추천 계산은 BE(POST /recommend, v3 카테고리)만 한다. FE 는 점수를 만들지 않는다.

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x))
}

/** 저장된 프로필 → 추천 요청. 필수 입력이 빠졌으면 호출 전에 missingForRecommend 로 막는다. */
export function toRecommendRequest(p: Profile): RecommendRequest {
  const dept = p.department.trim()
  const preset = DEPARTMENT_BUILDINGS[dept]
  return {
    upkeep_max_manwon: p.budgetManwon ?? 0,
    deposit_max_manwon: p.depositBudgetManwon,
    department: dept,
    // 예시표와 같으면 null 로 보내 BE 가 예시표를 쓰게 한다 (assumptions 에 출처가 남는다)
    department_buildings: preset && sameSet(preset, p.departmentBuildings) ? null : p.departmentBuildings,
    grade: p.grade ?? 1,
    categories: p.categories,
    answers: {
      weekly_commute_target_min: p.answers.weeklyCommuteTargetMin,
      gap_rest_min: p.answers.gapRestMin,
      first_class_buffer_min: p.answers.firstClassBufferMin,
    },
    timetable: p.timetable,
  }
}

export interface BuildingCoverage {
  name: string
  covered: number // 도보시간이 있는 매물 수
  total: number
}

/** 시간표에 고를 수 있는 건물 (BE GET /recommend/buildings). 도보시간이 있는 매물이 많은 순. */
export function getBuildings(signal?: AbortSignal): Promise<BuildingCoverage[]> {
  return apiFetch<BuildingCoverage[]>('/recommend/buildings', { signal })
}

export function recommend(req: RecommendRequest, signal?: AbortSignal): Promise<RecommendResult> {
  return apiFetch<RecommendResult>('/recommend', { method: 'POST', body: JSON.stringify(req), signal })
}
