// DB/poc_ewha.csv 열 구조와 1:1 대응. 빈 값은 null.
export interface Property {
  property_id: string
  university: string
  property_type: string
  address: string
  latitude: number | null
  longitude: number | null
  deposit_manwon: number | null
  monthly_rent_manwon: number | null
  maintenance_manwon: number | null
  area_m2: number | null
  source_url: string | null
  listing_checked_at: string | null
  spot1_name: string | null
  spot1_walk_min: number | null
  spot2_name: string | null
  spot2_walk_min: number | null
  spot3_name: string | null
  spot3_walk_min: number | null
  spot4_name: string | null
  spot4_walk_min: number | null
  monthly_housing_cost_manwon: number | null
}
