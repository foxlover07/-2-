"""매물 주변 정보 API (Phase 7). 추천 점수에는 쓰지 않는 참고 정보.

- GET /market/compare/{property_id}   : 광고 매물 ↔ 주변 전월세 실거래(국토부) 비교
- GET /market/amenities/{property_id} : 주변 생활 편의시설 (상가(상권)정보, 직선거리)
- GET /market/building/{property_id}  : 건축물대장 표제부 (사용승인일·층수·승강기·주차·주용도)
- GET /market/contract-check/{property_id} : 계약 전 확인 목록 + 이 매물의 사실 (판정 없음)
- GET /market/related-policies/{property_id} : 이 매물의 임대 형태·지역과 관련된 공식 정책 (판정 없음)
"""

from fastapi import APIRouter, HTTPException

from app.data import get_property
from app.amenities import AmenitiesResult, amenities_for
from app.building_register import BuildingInfo, building_for
from app.market import MarketCompare, compare_property
from app.policy import ContractCheck, RelatedPolicies, contract_check_for, related_policies_for

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/compare/{property_id}", response_model=MarketCompare)
def market_compare(property_id: str) -> MarketCompare:
    p = get_property(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"매물 {property_id} 을(를) 찾을 수 없습니다.")
    return compare_property(p)


@router.get("/amenities/{property_id}", response_model=AmenitiesResult)
def market_amenities(property_id: str) -> AmenitiesResult:
    p = get_property(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"매물 {property_id} 을(를) 찾을 수 없습니다.")
    return amenities_for(p)


@router.get("/building/{property_id}", response_model=BuildingInfo)
def market_building(property_id: str) -> BuildingInfo:
    p = get_property(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"매물 {property_id} 을(를) 찾을 수 없습니다.")
    return building_for(p)


@router.get("/contract-check/{property_id}", response_model=ContractCheck)
def market_contract_check(property_id: str) -> ContractCheck:
    p = get_property(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"매물 {property_id} 을(를) 찾을 수 없습니다.")
    return contract_check_for(p)


@router.get("/related-policies/{property_id}", response_model=RelatedPolicies)
def market_related_policies(property_id: str) -> RelatedPolicies:
    """이 매물의 임대 형태·지역과 관련된 공식 정책 안내 (자격 판정 없음)."""
    p = get_property(property_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"매물 {property_id} 을(를) 찾을 수 없습니다.")
    return related_policies_for(p)
