"""추천 API (추천 시스템 통합안 2026-11-13). LLM 없이 입력 JSON → 결과.

- POST /recommend           : 예산 · 이동 시간 · 면적 3요소 추천 (app.scoring)
- GET  /recommend/buildings : 시간표에 고를 수 있는 건물과, 도보시간이 있는 매물 수 (?university=, 기본 이화여자대학교)

v3 카테고리 추천(POST /recommend, GET /recommend/categories)과 POST /recommend/v2 는 통합안으로 합쳐 삭제했다.
도보시간은 매물 CSV 의 강의동 4개(spot1~4) + WALK_TABLE_PATH 의 도보시간 표(있으면)를 쓴다.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import building_walks
from app.buildings import aliases_for
from app.config import _read, get_settings
from app.data import load_properties, load_travel_times
from app.exclusions import KakaoPlaceSearcher
from app.place_search import place_searcher
from app.scoring import RecommendRequest, RecommendResult, UnknownBuildingError, recommend
from app.tools.kakao import KakaoClient

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendResult)
def recommend_integrated(req: RecommendRequest) -> RecommendResult:
    # 챗봇 장소 조건 확인: PLACE_SEARCH=local(기본) 이면 상가정보(호출 없음), kakao 면 카카오 키가 있을 때만
    uses_places = any(c.uses_places for plan in req.exclusions for c in plan.conditions)
    local = place_searcher() if uses_places else None
    kakao = uses_places and (_read("PLACE_SEARCH") or "local").strip().lower() == "kakao"
    key = get_settings().kakao_rest_api_key if kakao else None
    client = KakaoClient(key) if key else None
    try:
        props = load_properties()
        # 고른 건물의 도보시간이 없는 매물만 그때 계산 (app.building_walks, B 방식)
        fill = building_walks.fill_for_request(props, req, load_travel_times(props))
        result = recommend(
            props, req, travel=fill.travel,
            searcher=KakaoPlaceSearcher(client) if client else local,
        )
        result.notes.extend(fill.notes())
        return result
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
    building_type: str | None = None  # 기준표 분류 (lecture, library ...). 기준표에 없으면 None
    can_compute: bool = False  # 좌표가 있어 고르면 도보시간을 그때 계산할 수 있는지


@router.get("/buildings", response_model=list[BuildingCoverage])
def list_buildings(university: str = "이화여자대학교") -> list[BuildingCoverage]:
    """그 대학 건물 전체 (도보시간 표 + 건물 기준표 DB/ref). 도보시간이 있는 매물이 많은 건물부터.
    도보시간이 일부만 있는 건물(can_compute)은 고르면 추천할 때 빈 매물만 계산한다 (app.building_walks)."""
    props = [p for p in load_properties() if p.university == university]
    counts: dict[str, int] = {}
    for row in load_travel_times(props).values():
        for name in row:
            counts[name] = counts.get(name, 0) + 1
    cat = {b.name: b for b in building_walks.catalog(university)}
    for name in cat:
        counts.setdefault(name, 0)
    return [
        BuildingCoverage(name=n, covered=c, total=len(props), aliases=aliases_for(n),
                         building_type=cat[n].building_type if n in cat else None, can_compute=n in cat)
        for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ---------------------------------------------------------------- 시간표 강의동 → 도보시간 (7번, 2026-11-16)

class BuildingWalksRequest(BaseModel):
    university: str = "이화여자대학교"
    buildings: list[str] = Field(min_length=1, max_length=15, description="시간표·사진에서 읽은 건물 이름 (줄임말 가능)")
    property_ids: list[str] = Field(default_factory=list, max_length=50, description="이 매물들의 건물별 도보시간도 돌려준다")
    compute: bool = Field(False, description="True 면 도보시간이 없는 매물을 카카오로 계산 (요청 1번 150곳·하루 상한)")
    budget_manwon: float | None = Field(None, gt=0, description="계산할 매물을 유지비 상한(+여유) 안으로 줄인다")
    deposit_budget_manwon: float | None = Field(None, ge=0)


class BuildingWalkStat(BaseModel):
    input: str                 # 요청한 이름 그대로
    name: str | None           # 표준 이름 (모르는 건물이면 None)
    can_compute: bool          # 건물 기준표에 좌표가 있어 빈 매물을 계산할 수 있는지
    covered: int               # 도보시간이 있는 매물 수
    total: int
    min_walk_min: float | None
    median_walk_min: float | None


class BuildingWalksResponse(BaseModel):
    buildings: list[BuildingWalkStat]
    walks: dict[str, dict[str, float | None]]  # 매물 ID → 표준 건물 이름 → 편도 도보(분), 없으면 None
    computed: int = 0
    notes: list[str] = Field(default_factory=list)


@router.post("/building-walks", response_model=BuildingWalksResponse)
def building_walks_for(req: BuildingWalksRequest) -> BuildingWalksResponse:
    """시간표 건물 목록 → 건물별 도보시간 보유 현황과 (요청하면) 빈 매물 계산. 추천 순위와는 별개인 참고 정보."""
    from statistics import median

    from app.buildings import resolve
    from app.scoring import CONFIG, budget_exclusion_reason, deposit_exclusion_reason, monthly_fixed_cost

    props = [p for p in load_properties() if p.university == req.university]
    travel = load_travel_times(props)
    cat = {b.name for b in building_walks.catalog(req.university)}
    known = sorted(cat | {n for row in travel.values() for n in row})
    std = [(n, resolve(n, known)) for n in dict.fromkeys(x.strip() for x in req.buildings if x.strip())]
    names = [s for _, s in std if s in known]

    notes: list[str] = []
    computed = 0
    if req.compute and names:
        def keep(p) -> bool:
            if req.budget_manwon is None:
                return True
            cost = monthly_fixed_cost(p, CONFIG)
            if cost is None or budget_exclusion_reason(cost, req.budget_manwon, CONFIG):
                return False
            return not deposit_exclusion_reason(p.deposit_manwon, req.deposit_budget_manwon, CONFIG)

        fill = building_walks.fill_missing(props, req.university, names, travel, keep=keep)
        travel, computed = fill.travel, fill.computed
        notes = fill.notes()
        if not fill.computed and not fill.limited and not fill.failed:
            notes.append("계산할 매물이 없습니다 (이미 모두 있거나 예산 밖).")

    stats: list[BuildingWalkStat] = []
    for raw, s in std:
        ok = s in known
        vals = [row[s] for row in travel.values() if ok and s in row]
        stats.append(BuildingWalkStat(
            input=raw, name=s if ok else None, can_compute=s in cat, covered=len(vals), total=len(props),
            min_walk_min=min(vals) if vals else None, median_walk_min=round(median(vals), 1) if vals else None,
        ))
    unknown = [st.input for st in stats if st.name is None]
    if unknown:
        notes.append(f"위치를 모르는 건물이라 도보시간을 구할 수 없습니다: {', '.join(unknown)}.")
    walks = {pid: {n: travel.get(pid, {}).get(n) for n in names} for pid in req.property_ids}
    return BuildingWalksResponse(buildings=stats, walks=walks, computed=computed, notes=notes)
