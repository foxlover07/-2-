// BE app/decision/* (POST /analysis/pareto · tradeoff · sensitivity · relaxation) 응답과 1:1.
// 추천 순위는 바꾸지 않는 참고 분석이다.

export interface Summary {
  property_id: string
  rank: number
  score: number
  monthly_cost_manwon: number
  deposit_manwon: number | null
  travel_min: number | null
  area_m2: number
  over_budget: boolean
}

export interface ParetoItem extends Summary {
  pareto: boolean
  dominated_by: string | null
}

export interface ParetoResult {
  objectives: string[]
  objective_labels: Record<string, string>
  skipped_objectives: Record<string, string>
  candidates: number
  frontier: ParetoItem[]
  dominated_count: number
  top_k: ParetoItem[]
  notes: string[]
}

export interface Diff {
  monthly_cost_manwon: number
  deposit_manwon: number | null
  travel_min: number | null
  area_m2: number
}

export interface TradeoffResult {
  reference: Summary
  cheapest: Summary | null
  cheapest_vs_reference: Diff | null
  pay_more_for_commute: Array<{ extra_budget_manwon: number; best: Summary | null; travel_saved_min: number | null }>
  similar_commute_cheaper: { label: string; best: Summary | null; saved_manwon: number | null; diff: Diff | null }
  smaller_area_cheaper: Array<{ label: string; best: Summary | null; saved_manwon: number | null; diff: Diff | null }>
  notes: string[]
}

export interface SensitivityResult {
  top_k: number
  baseline_top: string[]
  baseline_candidates: number
  scenarios: Array<{
    scenario: { key: string; kind: string; label: string }
    candidates: number
    top: string[]
    top_retention: number | null
    kendall_tau: number | null
    dropped_from_top: string[]
    entered_top: string[]
  }>
  by_kind: Array<{ kind: string; label: string; scenarios: number; mean_top_retention: number | null; min_kendall_tau: number | null }>
  stable: Array<{ property_id: string; in_top: number; total: number; baseline_rank: number | null }>
  engine_calls: number
  notes: string[]
}

export interface RelaxOption {
  lever: string
  label: string
  change: Record<string, unknown>
  magnitude: number
  candidates: number
  added: number
  added_examples: Summary[]
  top: Summary[]
  reaches_target: boolean
}

export interface RelaxationResult {
  baseline_candidates: number
  target_count: number
  needs_relaxation: boolean
  failures: { by_stage: Record<string, number>; missing_walk_buildings: Record<string, number>; by_exclusion_condition: Record<string, number> }
  options: RelaxOption[]
  minimal: RelaxOption[]
  combined: RelaxOption | null
  locked: string[]
  engine_calls: number
  notes: string[]
}

export type AnalysisTab = 'pareto' | 'tradeoff' | 'sensitivity' | 'relaxation'
