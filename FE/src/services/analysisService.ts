import type { AnalysisTab, ParetoResult, RelaxationResult, SensitivityResult, TradeoffResult } from '../types/analysis'
import type { RecommendRequest } from '../types/recommend'
import { apiFetch } from './api'

// 의사결정 분석 (BE POST /analysis/*). 추천과 같은 요청을 그대로 넘긴다. 순위는 바꾸지 않는 참고 분석.

export type AnalysisResultMap = {
  pareto: ParetoResult
  tradeoff: TradeoffResult | null // 조건을 통과한 매물이 없으면 null
  sensitivity: SensitivityResult
  relaxation: RelaxationResult
}

export function runAnalysis<K extends AnalysisTab>(
  tab: K,
  request: RecommendRequest,
  signal?: AbortSignal,
): Promise<AnalysisResultMap[K]> {
  return apiFetch<AnalysisResultMap[K]>(`/analysis/${tab}`, {
    method: 'POST',
    body: JSON.stringify({ request }),
    signal,
  })
}
