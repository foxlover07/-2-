import type { Factor } from '../constants/recommend'
import type { RankedProperty } from '../types/recommend'

const num = (v: number) => Math.abs(v).toLocaleString('ko-KR', { maximumFractionDigits: 1 })

// 기준 대비 값 (통합안 3.6: 점수 대신 이 값을 보여 준다)
export function vsText(factor: Factor, item: RankedProperty): { text: string; good: boolean | null } {
  const v = item.vs_target
  if (factor === 'travel') {
    if (v.travel_min === null) return { text: '계산 불가', good: null }
    if (v.travel_min === 0) return { text: '희망 통학 시간과 같음', good: null }
    return v.travel_min < 0
      ? { text: `희망보다 ${num(v.travel_min)}분 가까움`, good: true }
      : { text: `희망보다 ${num(v.travel_min)}분 멂`, good: false }
  }
  if (factor === 'cost') {
    if (v.cost_manwon === 0) return { text: '예산과 같음', good: null }
    return v.cost_manwon < 0
      ? { text: `예산보다 ${num(v.cost_manwon)}만 원 쌈`, good: true }
      : { text: `예산보다 ${num(v.cost_manwon)}만 원 많음`, good: false }
  }
  if (v.area_m2 === 0) return { text: '희망 면적과 같음', good: null }
  return v.area_m2 > 0
    ? { text: `희망보다 ${num(v.area_m2)}㎡ 넓음`, good: null }
    : { text: `희망보다 ${num(v.area_m2)}㎡ 좁음`, good: false }
}

