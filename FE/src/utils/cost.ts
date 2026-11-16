import { MONTHLY_BUDGET_SLACK_MANWON } from '../constants/recommend'
import type { Property } from '../types/property'

// 월 고정 주거비 = 월세 + 관리비 (만원). 둘 중 하나라도 없으면 null.
// 부동소수점 오차(예: 70 + 9.9)를 없애기 위해 소수 둘째 자리에서 반올림한다.
export function getMonthlyFixedCost(p: Property): number | null {
  if (p.monthly_rent_manwon === null || p.maintenance_manwon === null) return null
  return Math.round((p.monthly_rent_manwon + p.maintenance_manwon) * 100) / 100
}

// 예산 판정 — 추천 엔진(scoring_config.monthly_budget_slack_manwon)과 같은 규칙:
//   유지비 ≤ 상한 → within, 상한 < 유지비 ≤ 상한 + 여유 → slack('예산 초과', 추천에는 남음),
//   유지비 > 상한 + 여유 → over(추천에서 제외). 비용을 알 수 없으면 판단하지 않고 null.
export type BudgetStatus = 'within' | 'slack' | 'over'

export function budgetStatus(p: Property, budgetManwon: number): BudgetStatus | null {
  const cost = getMonthlyFixedCost(p)
  if (cost === null) return null
  if (cost <= budgetManwon) return 'within'
  return cost <= budgetManwon + MONTHLY_BUDGET_SLACK_MANWON ? 'slack' : 'over'
}

// 월 고정 주거비가 가장 낮은 매물. 비용을 알 수 있는 매물이 없으면 null.
export function findCheapest(list: Property[]): Property | null {
  let best: Property | null = null
  let bestCost = Infinity
  for (const p of list) {
    const cost = getMonthlyFixedCost(p)
    if (cost !== null && cost < bestCost) {
      best = p
      bestCost = cost
    }
  }
  return best
}
