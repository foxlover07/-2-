"""추천 API. LLM 없이 입력 JSON → 결과.

- POST /recommend            : v3 카테고리 추천 (app.categories, 2026-11-08 팀 결정)
- GET  /recommend/categories : 카테고리 9개 목록과 지금 계산 가능 여부
- POST /recommend/v2         : 설계안 v2 (이동시간·교통 편의성·주거비 3요소). 비교용으로 남겨 둔다

이동시간은 서버에 있는 매물 CSV 의 강의동 4개(spot1~4) 도보시간을 쓴다.
"""

from fastapi import APIRouter, HTTPException

from app.categories import CategoryMeta, RecommendV3Request, RecommendV3Result, category_catalog, recommend_v3
from app.data import load_properties
from app.scoring import RecommendRequest, RecommendResult, UnknownBuildingError, recommend

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendV3Result)
def recommend_categories(req: RecommendV3Request) -> RecommendV3Result:
    try:
        return recommend_v3(load_properties(), req)
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/categories", response_model=list[CategoryMeta])
def list_categories() -> list[CategoryMeta]:
    return category_catalog()


@router.post("/v2", response_model=RecommendResult)
def recommend_v2(req: RecommendRequest) -> RecommendResult:
    try:
        return recommend(load_properties(), req)
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
