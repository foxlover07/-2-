import type { Property } from '../types/property'

// 같은 집일 수 있는 매물 묶음: 주소·보증금·월세·관리비·면적이 모두 같으면 같은 묶음으로 본다.
// 같은 건물의 다른 호실일 수도 있으므로 "같은 집일 수 있음"으로만 알리고 합치거나 지우지 않는다.
function key(p: Property): string | null {
  const parts = [p.address, p.deposit_manwon, p.monthly_rent_manwon, p.maintenance_manwon, p.area_m2]
  return parts.some((v) => v === null) ? null : parts.join('|')
}

/** 매물 ID → 같은 묶음의 매물 수 (2 이상인 매물만). */
export function duplicateCounts(list: Property[]): Map<string, number> {
  const groups = new Map<string, string[]>()
  for (const p of list) {
    const k = key(p)
    if (k === null) continue
    groups.set(k, [...(groups.get(k) ?? []), p.property_id])
  }
  const out = new Map<string, number>()
  for (const ids of groups.values()) {
    if (ids.length > 1) for (const id of ids) out.set(id, ids.length)
  }
  return out
}

export const DUPLICATE_HINT = '주소·보증금·월세·관리비·면적이 같은 매물이 더 있습니다. 같은 집의 중복 광고이거나 같은 건물의 다른 호실일 수 있습니다.'
