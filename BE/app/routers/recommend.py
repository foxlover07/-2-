"""추천 API (추천 시스템 통합안 2026-11-13). LLM 없이 입력 JSON → 결과.

- POST /recommend           : 예산 · 이동 시간 · 면적 3요소 추천 (app.scoring)
- GET  /recommend/buildings : 시간표에 고를 수 있는 건물과, 도보시간이 있는 매물 수 (?university=, 기본 이화여자대학교)

v3 카테고리 추천(POST /recommend, GET /recommend/categories)과 POST /recommend/v2 는 통합안으로 합쳐 삭제했다.
도보시간은 매물 CSV 의 강의동 4개(spot1~4) + WALK_TABLE_PATH 의 도보시간 표(있으면)를 쓴다.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.buildings import aliases_for
from app.config import get_settings
from app.data import load_properties, load_travel_times
from app.exclusions import KakaoPlaceSearcher
from app.scoring import RecommendRequest, RecommendResult, UnknownBuildingError, recommend
from app.tools.kakao import KakaoClient

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendResult)
def recommend_integrated(req: RecommendRequest) -> RecommendResult:
    # 챗봇 장소 조건이 있고 카카오 키가 있을 때만 장소 검색을 연결한다 (키가 없으면 notes 로 안내)
    uses_places = any(c.uses_places for plan in req.exclusions for c in plan.conditions)
    key = get_settings().kakao_rest_api_key if uses_places else None
    client = KakaoClient(key) if key else None
    try:
        props = load_properties()
        return recommend(
            props, req, travel=load_travel_times(props),
            searcher=KakaoPlaceSearcher(client) if client else None,
        )
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    finally:
        if client:
            client.close()


class BuildingCoverage(BaseModel):
    name: str
    covered: int  # 도보시간이 있는 매물 수
    total: int
    aliases: list[str]  # 이 건물로 읽는 줄임말 (초안, 팀 확인 필요)


@router.get("/buildings", response_model=list[BuildingCoverage])
def list_buildings(university: str = "이화여자대학교") -> list[BuildingCoverage]:
    """그 대학 매물 기준, 도보시간이 있는 매물이 많은 건물부터.
    매물 일부만 계산된 건물은 그 건물을 고르면 나머지 매물이 빠진다."""
    props = [p for p in load_properties() if p.university == university]
    counts: dict[str, int] = {}
    for row in load_travel_times(props).values():
        for name in row:
            counts[name] = counts.get(name, 0) + 1
    return [
        BuildingCoverage(name=n, covered=c, total=len(props), aliases=aliases_for(n))
        for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
