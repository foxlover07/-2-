from pydantic import BaseModel

AMENITY_KEYS = ("convenience", "supermarket", "laundry", "laundromat", "pharmacy", "clinic", "bar", "karaoke",
                "gym", "park", "subway", "bus_stop")


class Property(BaseModel):
    """FE/src/types/property.ts 의 Property 와 필드명·타입 1:1 대응. 빈 값은 None(null).

    아래 '선택 칸'은 통합 매물 CSV(integrated_listings.csv)에만 있다. PoC CSV 에는 없어서 기본값 None 이다.
    """

    property_id: str
    university: str
    property_type: str
    address: str
    latitude: float | None
    longitude: float | None
    deposit_manwon: float | None
    monthly_rent_manwon: float | None
    maintenance_manwon: float | None
    area_m2: float | None
    source_url: str | None = None
    listing_checked_at: str | None
    spot1_name: str | None
    spot1_walk_min: float | None
    spot2_name: str | None
    spot2_walk_min: float | None
    spot3_name: str | None
    spot3_walk_min: float | None
    spot4_name: str | None
    spot4_walk_min: float | None
    monthly_housing_cost_manwon: float | None

    # ---- 선택 칸: 출처·거래 가능 여부 (pipeline.check_listing_status 로 다시 확인한 결과를 덮어쓴다)
    source: str | None = None
    source_listing_id: str | None = None
    listing_status: str | None = None        # advertised_unverified(수집 때) / active / unavailable(거래 끝남·숨김)
    listing_status_detail: str | None = None  # 원 사이트 상태 (직방 close·cancel 등)
    listing_status_checked_at: str | None = None

    # ---- 선택 칸: 매물 상세 (jibsa 직방 목록·상세. 원본 표기 그대로)
    floor: str | None = None                 # "3", "반지하", "옥탑방", "지하1", "저층"/"중층"/"고층"
    building_floor: str | None = None        # 전체 층수 (문자열로 오기도 함)
    room_type: str | None = None             # 오픈형원룸 / 분리형원룸 / 복층형원룸 / 투룸 …

    # ---- 선택 칸: 주변 시설 (공공데이터로 계산. 가장 가까운 곳까지 m, 500m 안 개수.
    #      거리가 비어 있고 개수가 0 이면 '500m 안에 없음')
    nearest_m_convenience: float | None = None
    nearest_m_supermarket: float | None = None
    nearest_m_laundry: float | None = None
    nearest_m_laundromat: float | None = None
    nearest_m_pharmacy: float | None = None
    nearest_m_clinic: float | None = None
    nearest_m_bar: float | None = None
    nearest_m_karaoke: float | None = None
    count_500m_convenience: float | None = None
    count_500m_supermarket: float | None = None
    count_500m_laundry: float | None = None
    count_500m_laundromat: float | None = None
    count_500m_pharmacy: float | None = None
    count_500m_clinic: float | None = None
    count_500m_bar: float | None = None
    count_500m_karaoke: float | None = None
    # 추가 예정 칸 (공공데이터): 헬스장·공원·지하철역·버스정류장
    nearest_m_gym: float | None = None
    nearest_m_park: float | None = None
    nearest_m_subway: float | None = None
    nearest_m_bus_stop: float | None = None
    count_500m_gym: float | None = None
    count_500m_park: float | None = None
    count_500m_subway: float | None = None
    count_500m_bus_stop: float | None = None

    # ---- 선택 칸: 건축물대장 표제부 (bld_status 가 matched 일 때만 믿는다)
    bld_status: str | None = None
    bld_main_purpose: str | None = None      # 단독주택 / 공동주택 / 제2종근린생활시설 / 업무시설 …
    bld_use_approval_year: float | None = None
    bld_floors_above: float | None = None
    bld_elevators: float | None = None
    bld_parking: float | None = None
    bld_households: float | None = None
    bld_structure: str | None = None         # 철근콘크리트구조 / 벽돌구조 …
