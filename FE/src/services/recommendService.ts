import { DEPARTMENT_BUILDINGS, type Priorities } from '../constants/recommend'
import type { RecommendRequest, RecommendResult } from '../types/recommend'
import type { Profile } from '../utils/profile'
import { apiFetch } from './api'

// 추천 계산은 BE(POST /recommend)만 한다. FE 는 점수를 만들지 않는다.

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((x) => b.includes(x))
}

/** 저장된 프로필 → 추천 요청. 필수 입력이 빠졌으면 호출 전에 missingForRecommend 로 막는다. */
export function toRecommendRequest(p: Profile, priorities: Priorities = p.priorities): RecommendRequest {
  const dept = p.department.trim()
  const preset = DEPARTMENT_BUILDINGS[dept]
  return {
    budget_manwon: p.budgetManwon ?? 0,
    deposit_budget_manwon: p.depositBudgetManwon,
    department: dept,
    // 예시표와 같으면 null 로 보내 BE 가 예시표를 쓰게 한다 (assumptions 에 출처가 남는다)
    department_buildings: preset && sameSet(preset, p.departmentBuildings) ? null : p.departmentBuildings,
    grade: p.grade ?? 1,
    desired_commute_min: p.desiredCommuteMin,
    timetable: p.timetable,
    priorities,
  }
}

export function recommend(req: RecommendRequest, signal?: AbortSignal): Promise<RecommendResult> {
  return apiFetch<RecommendResult>('/recommend', { method: 'POST', body: JSON.stringify(req), signal })
}
