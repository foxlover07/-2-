import type { Property } from '../types/property'

// 월 고정 주거비 = 월세 + 관리비 (만원). 둘 중 하나라도 없으면 null.
// 부동소수점 오차(예: 70 + 9.9)를 없애기 위해 소수 둘째 자리에서 반올림한다.
export function getMonthlyFixedCost(p: Property): number | null {
  if (p.monthly_rent_manwon === null || p.maintenance_manwon === null) return null
  return Math.round((p.monthly_rent_manwon + p.maintenance_manwon) * 100) / 100
}

// 비용을 알 수 없으면 판단하지 않고 null을 반환한다.
export function isWithinBudget(p: Property, budgetManwon: number): boolean | null {
  const cost = getMonthlyFixedCost(p)
  if (cost === null) return null
  return cost <= budgetManwon
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
