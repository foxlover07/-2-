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
  source_url: string | null  // 통합 CSV 에는 없을 수 있다
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
  // 아래는 통합 매물 CSV 에만 있는 선택 칸 (BE models.Property 와 같은 이름)
  floor?: string | null
  building_floor?: string | null
  room_type?: string | null
  nearest_m_convenience?: number | null
  nearest_m_supermarket?: number | null
  nearest_m_laundry?: number | null
  nearest_m_laundromat?: number | null
  nearest_m_pharmacy?: number | null
  nearest_m_clinic?: number | null
  nearest_m_bar?: number | null
  nearest_m_karaoke?: number | null
  count_500m_convenience?: number | null
  count_500m_supermarket?: number | null
  count_500m_laundry?: number | null
  count_500m_laundromat?: number | null
  count_500m_pharmacy?: number | null
  count_500m_clinic?: number | null
  count_500m_bar?: number | null
  count_500m_karaoke?: number | null
  nearest_m_gym?: number | null
  nearest_m_park?: number | null
  nearest_m_subway?: number | null
  nearest_m_bus_stop?: number | null
  count_500m_gym?: number | null
  count_500m_park?: number | null
  count_500m_subway?: number | null
  count_500m_bus_stop?: number | null
  bld_status?: string | null
  bld_main_purpose?: string | null
  bld_use_approval_year?: number | null
  bld_floors_above?: number | null
  bld_elevators?: number | null
  bld_parking?: number | null
  bld_households?: number | null
  bld_structure?: string | null
}
