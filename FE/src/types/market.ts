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
