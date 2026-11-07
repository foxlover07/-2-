import type { Property } from '../types/property'
import { getMonthlyFixedCost } from './cost'

// 가격은 보증금 상한 + 유지비(월세 + 관리비) 상한으로만 거른다 (2026-11-08 결정).
export interface PropertyFilters {
  depositMax: number | null
  costMax: number | null // 유지비 상한
  areaMin: number | null
  areaMax: number | null
  types: string[]
}

export const EMPTY_FILTERS: PropertyFilters = {
  depositMax: null,
  areaMin: null,
  areaMax: null,
  types: [],
  costMax: null,
}

export interface NumberRange {
  min: number
  max: number
}

export interface FilterBounds {
  deposit: NumberRange | null
  area: NumberRange | null
  cost: NumberRange | null
  types: string[]
}

function rangeOf(values: Array<number | null>): NumberRange | null {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length === 0) return null
  return { min: Math.min(...nums), max: Math.max(...nums) }
}

// 필터 입력의 안내값과 주거 형태 선택지는 데이터에서 뽑는다.
export function getFilterBounds(list: Property[]): FilterBounds {
  return {
    deposit: rangeOf(list.map((p) => p.deposit_manwon)),
    area: rangeOf(list.map((p) => p.area_m2)),
    cost: rangeOf(list.map(getMonthlyFixedCost)),
    types: [...new Set(list.map((p) => p.property_type))].sort((a, b) => a.localeCompare(b, 'ko')),
  }
}

// 조건이 걸려 있는데 값이 없으면 조건을 만족하는지 알 수 없으므로 제외한다.
function inRange(value: number | null, min: number | null, max: number | null): boolean {
  if (min === null && max === null) return true
  if (value === null) return false
  return (min === null || value >= min) && (max === null || value <= max)
}

export function filterProperties(list: Property[], f: PropertyFilters): Property[] {
  return list.filter(
    (p) =>
      inRange(p.deposit_manwon, null, f.depositMax) &&
      inRange(p.area_m2, f.areaMin, f.areaMax) &&
      inRange(getMonthlyFixedCost(p), null, f.costMax) &&
      (f.types.length === 0 || f.types.includes(p.property_type)),
  )
}

export const SORT_OPTIONS = [
  { key: 'cost', label: '유지비 낮은순' },
  { key: 'rent', label: '월세 낮은순' },
  { key: 'deposit', label: '보증금 낮은순' },
  { key: 'area', label: '면적 넓은순' },
] as const

export type SortKey = (typeof SORT_OPTIONS)[number]['key']

const SORT_VALUE: Record<SortKey, (p: Property) => number | null> = {
  cost: getMonthlyFixedCost,
  rent: (p) => p.monthly_rent_manwon,
  deposit: (p) => p.deposit_manwon,
  area: (p) => (p.area_m2 === null ? null : -p.area_m2),
}

// 값이 없는 매물은 뒤로 보낸다. 같으면 매물 ID 순.
export function sortProperties(list: Property[], key: SortKey): Property[] {
  const valueOf = SORT_VALUE[key]
  return [...list].sort((a, b) => {
    const va = valueOf(a)
    const vb = valueOf(b)
    if (va !== vb) {
      if (va === null) return 1
      if (vb === null) return -1
      return va - vb
    }
    return a.property_id.localeCompare(b.property_id)
  })
}
