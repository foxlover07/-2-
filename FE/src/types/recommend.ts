// BE app/scoring.py 의 RecommendRequest / RecommendResult 와 1:1 대응 (추천 시스템 통합안).
import type { Day, Factor, LargerArea } from '../constants/recommend'

// 챗봇 확인 계획 (BE app/exclusions.ExclusionPlan). ⚠️ 챗봇은 미완성 — 지금 화면은 빈 목록을 보낸다.
export interface ExclusionPlan {
  source_text: string
  conditions: Array<{
    tool: 'nearby_count' | 'nearest_distance' | 'listing_field'
    label: string
    keywords?: string[]
    category?: string | null
    radius_m?: number | null
    field?: 'area_m2' | 'deposit_manwon' | 'monthly_rent_manwon' | 'maintenance_manwon' | null
    exclude_if: { op: '>' | '>=' | '<' | '<=' | '==' | '!='; value: number }
  }>
  unsupported: string[]
  confirm_message: string
}

export interface RecommendRequest {
  university: string // 이 대학 매물만 추천 (매물 데이터의 university 값)
  department: string
  department_buildings: string[] | null // null 이면 BE 의 학과 → 건물 예시표 사용 (이대만)
  grade: number
  priorities: [Factor, Factor] // 1·2순위. 3순위는 자동
  budget_manwon: number // 유지비 상한
  deposit_budget_manwon: number | null
  desired_commute_min: number
  desired_area_m2: number
  larger_area: LargerArea
  timetable: Array<{ day: Day; start: string; end: string; building: string }>
  exclusions: ExclusionPlan[]
}

export interface TravelPart {
  avg_min: number
  target_min: number
  score: number
  weight: number
}

export interface RankedProperty {
  rank: number
  property_id: string
  score: number
  factor_scores: Record<Factor, number | null>
  contributions: Partial<Record<Factor, number>>
  travel_parts: Partial<Record<'department' | 'timetable' | 'frequent', TravelPart>>
  vs_target: {
    travel_min: number | null // 편도 평균 − 희망 (음수 = 가까움)
    cost_manwon: number // 유지비 − 상한 (음수 = 쌈)
    area_m2: number // 면적 − 희망 (양수 = 넓음)
  }
  monthly_cost_manwon: number
  deposit_manwon: number | null
  area_m2: number
  over_budget: boolean
}

export interface Reason {
  factor: Factor
  label: string
  part: string | null
  part_label: string | null
  value: number | null
  top1_value: number | null
  target: number | null
  unit: string
  score: number
  top1_score: number
  weighted_gap: number
}

export interface NotRecommended {
  property_id: string
  best_rank_elsewhere: number
  top_in: Array<{ priorities: [Factor, Factor]; rank: number }>
  weak: Reason
  strong: Reason | null
  message: string
}

export type ExcludedStage = 'duplicate' | 'budget' | 'deposit' | 'exclusion' | 'missing'

export interface RecommendResult {
  priorities: [Factor, Factor, Factor]
  weights: Record<Factor, number>
  ranked: RankedProperty[]
  not_recommended: NotRecommended[]
  excluded: Array<{ property_id: string; reason: string; stage: ExcludedStage; condition: string | null }>
  exclusion_counts: Record<string, number>
  assumptions: Record<string, unknown>
  notes: string[]
}
