// BE app/categories.py 의 RecommendV3Request / RecommendV3Result 와 1:1 대응.
import type { CategoryKey, Day } from '../constants/recommend'

export interface RecommendRequest {
  university: string // 이 대학 매물만 추천 (매물 데이터의 university 값)
  upkeep_max_manwon: number
  deposit_max_manwon: number | null
  department: string
  department_buildings: string[] | null // null 이면 BE 의 학과 → 건물 예시표 사용
  grade: number
  categories: CategoryKey[] // 1·2·3순위 순서
  answers: {
    weekly_commute_target_min: number
    gap_rest_min: number
    first_class_buffer_min: number
  }
  timetable: Array<{ day: Day; start: string; end: string; building: string }>
}

export interface RankedProperty {
  rank: number
  property_id: string
  score: number | null // 계산할 수 있는 기준이 없으면 null
  category_scores: Record<string, number | null>
  category_values: Record<string, string | null> // "주 62분", "3 / 3회", "8:44 출발"
  upkeep_manwon: number
  deposit_manwon: number | null
  top: boolean
}

export interface RecommendResult {
  categories: string[]
  weights: Record<string, number>
  top_k: number
  ranked: RankedProperty[]
  excluded: Array<{ property_id: string; reason: string }>
  notes: string[]
  assumptions: Record<string, unknown>
}
