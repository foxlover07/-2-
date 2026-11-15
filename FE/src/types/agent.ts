// BE app/agent/schemas.py 의 AgentProfile / ChatRequest / ChatResponse 와 1:1 대응 (통합안 엔진).
import type { Day, Factor, LargerArea } from '../constants/recommend'
import type { ExclusionPlan } from './recommend'

export interface AgentProfile {
  university: string | null
  budget_manwon: number | null
  deposit_budget_manwon: number | null
  department: string | null
  department_buildings: string[]
  grade: number | null
  priorities: Factor[] // 1·2순위
  desired_commute_min: number | null
  desired_area_m2: number | null
  larger_area: LargerArea | null
  timetable: Array<{ day: Day; start: string; end: string; building: string }>
  exclusions: ExclusionPlan[]
}

export interface AgentTopItem {
  rank: number
  property_id: string
  score: number
  factor_scores: Record<string, number | null>
  contributions: Record<string, number>
  monthly_cost_manwon: number
  deposit_manwon: number | null
  area_m2: number
  travel_min: number | null // 편도 평균 도보시간
  vs_target: { travel_min: number | null; cost_manwon: number; area_m2: number }
  over_budget: boolean
  address: string | null
  property_type: string | null
  source_url: string | null
  walk_min: Record<string, number>
  same_house_ids: string[] // 같은 집일 수 있는 다른 광고 (TOP 에서는 묶어 하나만)
}

export interface AgentRecSummary {
  university: string
  priorities: string[] // 1·2·3순위
  weights: Record<string, number>
  total_ranked: number
  excluded_count: number
  top: AgentTopItem[]
  notes: string[]
  exclusion_counts: Record<string, number>
}

export type AnalysisKind = 'pareto' | 'tradeoff' | 'sensitivity' | 'relaxation' | 'market' | 'amenities' | 'building'

// 분석 API(/analysis/*, /market/*) 응답과 같은 형식. 추천 순위는 바꾸지 않는 참고 분석.
export interface AgentAnalysis {
  kind: AnalysisKind
  property_id: string | null
  result: Record<string, unknown> | null
}

export interface AgentComparison {
  label_before: string
  label_after: string
  total_before: number
  total_after: number
  top_before: string[]
  top_after: string[]
  entered_top: string[]
  left_top: string[]
  score_changes: Array<{
    property_id: string
    rank_before: number | null
    score_before: number | null
    rank_after: number | null
    score_after: number | null
  }>
  profile_diff: Record<string, { before: unknown; after: unknown }>
}

export type AgentStatus = 'ok' | 'need_info' | 'confirm' | 'llm_unavailable' | 'llm_error' | 'error'

export interface ChatResponse {
  session_id: string
  assistant_message: string
  updated_profile: AgentProfile | null
  profile_changed: boolean
  recommendation_results: AgentRecSummary | null
  comparison_results: AgentComparison | null
  analysis_results: AgentAnalysis | null
  policy_results: PolicyResult | null
  missing_fields: string[]
  intent: string | null
  status: AgentStatus
  // status=confirm: 바뀔 조건 (필드 → before/after). 네/아니요로 답하면 이어서 처리된다
  pending_change: Record<string, { before: unknown; after: unknown }> | null
  trace: Record<string, unknown> | null
}

// 시간표 사진 입력 (BE POST /agent/timetable-image). 저장하지 않고, 확인 화면에서 고친 뒤 시간표에 넣는다.
export interface TimetableSlotRead {
  day: Day
  start: string
  end: string
  building: string | null // 표준 건물 이름. 못 맞추면 null → 사용자가 고른다
  building_text: string // 이미지에서 읽은 표기
  course: string | null
  issue: string | null
}

export interface TimetableImageResponse {
  status: 'ok' | 'llm_unavailable' | 'llm_error' | 'ocr_unavailable' | 'ocr_error'
  message: string
  slots: TimetableSlotRead[]
  notes: string[]
  buildings: string[]
}

// 주거 지원 정책 안내 (BE app/policy.PolicyResult). 자격을 판정하지 않는 안내용 정보.
export interface PolicyItem {
  policy_id: string
  name: string
  summary: string | null
  support: string | null
  subcategory: string | null
  agency: string | null
  region: string | null
  target_age: string | null
  apply_period: string | null
  apply_url: string | null
  ref_url: string | null
  updated_at: string | null
}

export interface PolicyResult {
  available: boolean
  query: string
  items: PolicyItem[]
  total_matched: number
  reason: string | null
  source: string
  fetched_at: string | null
  official_links: Array<{ name: string; url: string }>
  disclaimer: string
}
