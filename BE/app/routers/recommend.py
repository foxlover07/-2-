"""추천 API. LLM 없이 입력 JSON → 결과.

- POST /recommend            : v3 카테고리 추천 (app.categories, 2026-11-08 팀 결정)
- GET  /recommend/categories : 카테고리 9개 목록과 지금 계산 가능 여부
- GET  /recommend/buildings  : 시간표에 고를 수 있는 건물과, 도보시간이 있는 매물 수
- POST /recommend/v2         : 설계안 v2 (이동시간·교통 편의성·주거비 3요소). 비교용으로 남겨 둔다

도보시간은 매물 CSV 의 강의동 4개(spot1~4) + WALK_TABLE_PATH 의 도보시간 표(있으면)를 쓴다.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.categories import CategoryMeta, RecommendV3Request, RecommendV3Result, category_catalog, recommend_v3
from app.data import load_properties, load_travel_times
from app.scoring import RecommendRequest, RecommendResult, UnknownBuildingError, recommend

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendV3Result)
def recommend_categories(req: RecommendV3Request) -> RecommendV3Result:
    try:
        props = load_properties()
        return recommend_v3(props, req, travel=load_travel_times(props))
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/categories", response_model=list[CategoryMeta])
def list_categories() -> list[CategoryMeta]:
    return category_catalog()


class BuildingCoverage(BaseModel):
    name: str
    covered: int  # 도보시간이 있는 매물 수
    total: int


@router.get("/buildings", response_model=list[BuildingCoverage])
def list_buildings() -> list[BuildingCoverage]:
    """도보시간이 있는 매물이 많은 건물부터. 매물 일부만 계산된 건물은 그 건물을 고르면 나머지 매물이 빠진다."""
    props = load_properties()
    counts: dict[str, int] = {}
    for row in load_travel_times(props).values():
        for name in row:
            counts[name] = counts.get(name, 0) + 1
    return [
        BuildingCoverage(name=n, covered=c, total=len(props))
        for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@router.post("/v2", response_model=RecommendResult)
def recommend_v2(req: RecommendRequest) -> RecommendResult:
    try:
        return recommend(load_properties(), req)
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
