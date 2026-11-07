// BE app/scoring.py 의 RecommendRequest / RecommendResult 와 1:1 대응.
import type { Day, Factor } from '../constants/recommend'

export interface RecommendRequest {
  budget_manwon: number
  deposit_budget_manwon: number | null
  department: string
  department_buildings: string[] | null // null 이면 BE 의 학과 → 건물 예시표 사용
  grade: number
  desired_commute_min: number
  timetable: Array<{ day: Day; start: string; end: string; building: string }>
  priorities: [Factor, Factor]
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
  factor_scores: Record<string, number | null>
  contributions: Record<string, number>
  travel_parts: Record<string, TravelPart>
  monthly_cost_manwon: number
  deposit_manwon: number | null
  over_budget: boolean
}

export interface Reason {
  factor: string
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
  top_in: Array<{ priorities: [string, string]; rank: number }>
  weak: Reason
  strong: Reason | null
  message: string
}

export interface RecommendResult {
  priorities: [string, string, string]
  weights: Record<string, number>
  ranked: RankedProperty[]
  not_recommended: NotRecommended[]
  excluded: Array<{ property_id: string; reason: string }>
  assumptions: Record<string, unknown>
  notes: string[]
}
