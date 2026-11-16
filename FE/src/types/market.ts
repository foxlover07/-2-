import type { PolicyItem } from './agent'
// BE GET /market/compare/{id}, /market/amenities/{id} 응답 (BE/app/market.py, app/amenities.py 와 1:1).
// 추천 점수에는 쓰지 않는 참고 정보다.

export interface CompSample {
  contract_date: string
  umd_nm: string
  tx_type: 'sh' | 'rh' | 'offi' | string
  area_m2: number
  deposit_manwon: number
  monthly_rent_manwon: number
}

export interface MarketCompare {
  property_id: string
  available: boolean
  reason: string | null
  scope: '동' | '구' | null
  criteria: {
    types?: string[]
    umd_nm?: string | null
    area_m2?: [number, number]
    deposit_manwon?: [number, number] | null
    since?: string
    until?: string
  }
  n: number
  listing_monthly_rent_manwon: number | null
  listing_maintenance_manwon: number | null
  rent_median: number | null
  rent_p25: number | null
  rent_p75: number | null
  deposit_median: number | null
  listing_percentile: number | null
  level: string | null
  period: string[]
  recent: CompSample[]
  source: string
  fetched_at: string | null
  notes: string[]
}

export interface AmenityCategory {
  category: string
  label: string
  group: 'daily' | 'health' | 'context'
  nearest: { name: string; address: string; distance_m: number } | null
  counts: Record<string, number>
}

export interface Amenities {
  property_id: string
  available: boolean
  reason: string | null
  radii_m: number[]
  categories: AmenityCategory[]
  stdr_ym: string[]
  source: string
  notes: string[]
}

// BE GET /market/building/{id} (app/building_register.py). 광고 지번 기준 건축물대장 표제부.
export interface BuildingInfo {
  property_id: string
  available: boolean
  match_status: 'matched' | 'multiple' | 'no_record' | 'dong_unresolved' | 'error' | null
  reason: string | null
  building_name: string | null
  main_purpose: string | null
  detail_purpose: string | null
  use_approval_date: string | null
  use_approval_year: number | null
  floors_above: number | null
  floors_below: number | null
  elevators: number | null
  parking: number | null
  households: number | null
  structure: string | null
  total_area_m2: number | null
  source: string
  fetched_at: string | null
  notes: string[]
}

// 계약 전 확인 (BE app/policy.ContractCheck). 일반 점검 항목 + 이 매물의 사실. 판정하지 않는다.
export interface ContractCheckItem {
  key: string | null
  text: string
  link: { name: string; url: string } | null
  fact: string | null // 서비스가 이 매물에 대해 가진 사실. null = 붙일 데이터 없음
}

export interface ContractCheck {
  property_id: string
  phases: Array<{ phase: string; items: ContractCheckItem[] }>
  source: string
  notes: string[]
}

// 매물과 관련된 공식 정책 (BE app/policy.RelatedPolicies). 임대 형태·지역으로 고른 안내. 자격 판정 없음.
export interface RelatedPolicies {
  property_id: string
  rent_kind: '월세' | '전세' | null
  regions: string[]
  available: boolean
  items: PolicyItem[]
  reason: string | null
  source: string
  fetched_at: string | null
  notes: string[]
  official_links: Array<{ name: string; url: string }>
  disclaimer: string
}
