from pydantic import BaseModel


class Property(BaseModel):
    """FE/src/types/property.ts 의 Property 와 필드명·타입 1:1 대응. 빈 값은 None(null)."""

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
    source_url: str | None
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
