"""5장 챗봇 제외 조건: 확인 도구 3개 + exclusions 실행 (계획 JSON 을 직접 넣어 테스트, 카카오는 가짜).

⚠️ 챗봇(말 → 확인 계획)은 미완성이라 여기서는 다루지 않는다.
"""

from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.data import load_properties
from app.exclusions import Condition, ExclusionPlan, ExclusionRunner, KakaoPlaceSearcher
from app.main import app
from app.scoring import RecommendRequest, recommend
from app.scoring_config import CONFIG
from app.tools.kakao import KakaoClient

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}
# PoC 매물 10개는 좌표가 비어 있어 장소 도구 테스트에는 좌표를 넣은 복사본을 쓴다
WITH_COORDS = [p.model_copy(update={"latitude": 37.56 + i * 0.001, "longitude": 126.94}) for i, p in enumerate(PROPS)]

NOISE = {
    "source_text": "밤에 시끄러우면 잠을 못 자요",
    "conditions": [{
        "tool": "nearby_count", "label": "술집", "keywords": ["술집", "호프", "클럽"], "radius_m": 150,
        "exclude_if": {"op": ">", "value": 2},
    }],
    "unsupported": [],
    "confirm_message": "주변 150m 안에 술집이 3곳 이상인 매물을 뺄게요. 괜찮으세요?",
}


class FakeSearcher:
    """매물 위도로 결과를 정한다. places[(위도 반올림, 키워드)] = [(id, 거리m), ...]"""

    def __init__(self, places, *, fail=False):
        self.places = places
        self.calls = []
        self.fail = fail

    def search(self, *, lat, lng, radius_m, query, category, page, size):
        self.calls.append((round(lat, 3), query, category, radius_m, page))
        if self.fail:
            raise httpx.ConnectError("down")
        found = [(i, d) for i, d in self.places.get((round(lat, 3), query or category), []) if d <= radius_m]
        found.sort(key=lambda x: x[1])
        chunk = found[(page - 1) * size: page * size]
        return {"documents": [{"id": i, "distance": str(d)} for i, d in chunk],
                "meta": {"is_end": page * size >= len(found)}}


def lat_of(pid):
    return round(next(p for p in WITH_COORDS if p.property_id == pid).latitude, 3)


def req(**overrides) -> RecommendRequest:
    base = dict(department="컴퓨터공학전공", grade=2, priorities=("travel", "cost"), budget_manwon=200,
                desired_commute_min=15, desired_area_m2=20)
    return RecommendRequest(**{**base, **overrides})


# ---------------------------------------------------------------- 계획 형식

def test_plan_format_from_design_parses():
    plan = ExclusionPlan(**NOISE)
    assert plan.conditions[0].tool == "nearby_count" and plan.conditions[0].radius_m == 150


def test_condition_needs_fields_for_its_tool():
    with pytest.raises(ValidationError):
        Condition(tool="nearby_count", label="술집", keywords=["술집"], exclude_if={"op": ">", "value": 2})  # 반경 없음
    with pytest.raises(ValidationError):
        Condition(tool="nearest_distance", label="편의점", exclude_if={"op": ">", "value": 300})  # 키워드·카테고리 없음
    with pytest.raises(ValidationError):
        Condition(tool="listing_field", label="면적", exclude_if={"op": "<", "value": 16})  # field 없음
    with pytest.raises(ValidationError):
        Condition(tool="nearby_count", label="x", keywords=["x"], radius_m=30000, exclude_if={"op": ">", "value": 1})
    with pytest.raises(ValidationError):
        Condition(tool="listing_field", label="면적", field="area_m2", exclude_if={"op": "~", "value": 1})


# ---------------------------------------------------------------- nearby_count

def test_nearby_count_excludes_with_reason_and_counts_unique_places():
    p1, p2 = lat_of("P002"), lat_of("P005")
    searcher = FakeSearcher({
        (p1, "술집"): [("a", 50), ("b", 90), ("c", 140)],
        (p1, "호프"): [("b", 90), ("d", 120), ("e", 149)],   # b 는 중복
        (p1, "클럽"): [("f", 400)],                           # 반경 밖
        (p2, "술집"): [("x", 100)],
    })
    r = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=searcher, exclusion_cache={})
    by = {e.property_id: e for e in r.excluded}
    assert by["P002"].reason == "주변 150m 안 술집 5곳"
    assert by["P002"].stage == "exclusion" and by["P002"].condition == "술집"
    assert "P005" not in by                                   # 1곳 ≤ 2
    assert r.exclusion_counts == {"술집": 1}
    assert any("챗봇 조건으로 매물 1개를 뺐습니다" in n for n in r.notes)


def test_exclusions_run_after_budget_filter():
    searcher = FakeSearcher({})
    recommend(WITH_COORDS, req(budget_manwon=75, exclusions=[NOISE]), searcher=searcher, exclusion_cache={})
    checked = {c[0] for c in searcher.calls}
    # 예산으로 먼저 빠진 P001(87), P004(145), P009(135) 는 카카오를 부르지 않는다
    assert lat_of("P004") not in checked and lat_of("P009") not in checked and lat_of("P001") not in checked
    assert lat_of("P002") in checked


def test_same_plan_same_result_and_cache():
    cache: dict = {}
    s1 = FakeSearcher({(lat_of("P002"), "술집"): [("a", 1), ("b", 2), ("c", 3)]})
    a = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=s1, exclusion_cache=cache)
    s2 = FakeSearcher({})  # 캐시가 있으면 다시 부르지 않는다
    b = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=s2, exclusion_cache=cache)
    assert [e.model_dump() for e in a.excluded] == [e.model_dump() for e in b.excluded]
    assert s2.calls == []


# ---------------------------------------------------------------- nearest_distance

def test_nearest_distance_far_or_missing_is_excluded():
    plan = {"conditions": [{"tool": "nearest_distance", "label": "편의점", "category": "CS2", "radius_m": 500,
                            "exclude_if": {"op": ">", "value": 300}}]}
    searcher = FakeSearcher({
        (lat_of("P002"), "CS2"): [("s", 420)],
        (lat_of("P005"), "CS2"): [("t", 80)],
    })
    r = recommend(WITH_COORDS, req(exclusions=[plan]), searcher=searcher, exclusion_cache={})
    reasons = {e.property_id: e.reason for e in r.excluded}
    assert reasons["P002"] == "가장 가까운 편의점까지 420m"
    assert "P005" not in reasons
    assert reasons["P006"] == "반경 500m 안 편의점 없음"     # 없으면 무한대로 비교


def test_nearest_distance_default_radius_from_config():
    plan = {"conditions": [{"tool": "nearest_distance", "label": "편의점", "keywords": ["편의점"],
                            "exclude_if": {"op": ">", "value": 300}}]}
    searcher = FakeSearcher({})
    recommend(WITH_COORDS[:2], req(exclusions=[plan]), searcher=searcher, exclusion_cache={})
    assert {c[3] for c in searcher.calls} == {CONFIG.nearest_search_radius_m}


# ---------------------------------------------------------------- listing_field

def test_listing_field_runs_without_kakao():
    plan = {"conditions": [{"tool": "listing_field", "label": "좁은 방", "field": "area_m2",
                            "exclude_if": {"op": "<", "value": 15}}]}
    r = recommend(PROPS, req(exclusions=[plan]))
    reasons = {e.property_id: e.reason for e in r.excluded if e.stage == "exclusion"}
    assert reasons == {"P003": "면적 13.22㎡ (조건: 면적 < 15㎡)"}


def test_listing_field_missing_value_is_kept_and_noted():
    plan = {"conditions": [{"tool": "listing_field", "label": "관리비", "field": "maintenance_manwon",
                            "exclude_if": {"op": ">", "value": 10}}]}
    # P004 관리비 15 > 10 → 제외, P005 관리비 5 → 통과, P002 관리비 없음 → 확인 못 함 (뒤의 '정보 없음' 필터에서 빠짐)
    props = [BY_ID["P002"].model_copy(update={"maintenance_manwon": None}), BY_ID["P004"], BY_ID["P005"]]
    r = recommend(props, req(exclusions=[plan]))
    assert {e.property_id: e.stage for e in r.excluded} == {"P004": "exclusion", "P002": "missing"}
    assert any("정보가 없어 확인하지 못한 매물 1개" in n for n in r.notes)


# ---------------------------------------------------------------- 5.4장 규칙

def test_no_coordinates_kept_and_noted():
    searcher = FakeSearcher({})
    r = recommend(PROPS, req(exclusions=[NOISE]), searcher=searcher, exclusion_cache={})   # PoC 는 좌표 없음
    assert not [e for e in r.excluded if e.stage == "exclusion"]
    assert any("좌표가 없어 장소 조건을 확인하지 못한 매물 10개" in n for n in r.notes)
    assert searcher.calls == []


def test_no_kakao_key_place_conditions_noted():
    r = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=None)
    assert not [e for e in r.excluded if e.stage == "exclusion"]
    assert any("카카오 키가 없어 장소 조건(술집)" in n for n in r.notes)


def test_call_limit():
    searcher = FakeSearcher({})
    cfg = replace(CONFIG, kakao_call_limit=4)   # 키워드 3개 → 매물 1개 + 1번만
    r = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=searcher, exclusion_cache={}, cfg=cfg)
    assert len(searcher.calls) == 4
    assert r.assumptions["exclusions"]["kakao_calls"] == 4
    assert any("카카오 호출 상한(4회)" in n and "조건을 줄여 주세요" in n for n in r.notes)


def test_search_error_does_not_exclude():
    r = recommend(WITH_COORDS, req(exclusions=[NOISE]), searcher=FakeSearcher({}, fail=True), exclusion_cache={})
    assert not [e for e in r.excluded if e.stage == "exclusion"]
    assert any("장소 검색 오류로 확인하지 못한 매물" in n for n in r.notes)


def test_unsupported_is_not_guessed():
    plan = {"conditions": [], "unsupported": ["경사가 심한 곳"]}
    r = recommend(PROPS, req(exclusions=[plan]))
    assert not [e for e in r.excluded if e.stage == "exclusion"]
    assert any("'경사가 심한 곳'은(는) 아직 확인할 수 없어요" in n for n in r.notes)


def test_everything_excluded_by_conditions_is_explained():
    plan = {"conditions": [{"tool": "listing_field", "label": "전부", "field": "area_m2",
                            "exclude_if": {"op": ">", "value": 0}}]}
    r = recommend(PROPS, req(exclusions=[plan]))
    assert r.ranked == []
    assert r.exclusion_counts == {"전부": 10}
    assert any("조건 때문에 남은 매물이 없습니다" in n for n in r.notes)


def test_assumptions_list_approved_plans():
    a = recommend(PROPS, req(exclusions=[NOISE])).assumptions["exclusions"]
    assert a["plans"][0]["source_text"] == "밤에 시끄러우면 잠을 못 자요"
    assert a["kakao_call_limit"] == 500


# ---------------------------------------------------------------- 카카오 클라이언트 · API

def test_kakao_places_near_sends_lng_as_x_and_uses_category_endpoint():
    seen = []

    def handler(r: httpx.Request):
        seen.append((r.url.path, dict(r.url.params)))
        return httpx.Response(200, json={"documents": [{"id": "1", "distance": "42"}], "meta": {"is_end": True}})

    searcher = KakaoPlaceSearcher(KakaoClient("test-key", transport=httpx.MockTransport(handler)))
    searcher.search(lat=37.5, lng=126.9, radius_m=150, query="술집", category=None, page=1, size=15)
    searcher.search(lat=37.5, lng=126.9, radius_m=500, query=None, category="CS2", page=1, size=1)
    assert seen[0][0] == "/v2/local/search/keyword.json"
    assert seen[0][1]["x"] == "126.9" and seen[0][1]["y"] == "37.5" and seen[0][1]["radius"] == "150"
    assert seen[1][0] == "/v2/local/search/category.json" and seen[1][1]["category_group_code"] == "CS2"


def test_api_accepts_exclusions(monkeypatch):
    import app.routers.recommend as router

    monkeypatch.setattr(router, "get_settings", lambda: type("S", (), {"kakao_rest_api_key": None})())
    body = {"department": "컴퓨터공학전공", "grade": 2, "priorities": ["travel", "cost"], "budget_manwon": 200,
            "desired_commute_min": 15, "desired_area_m2": 20, "exclusions": [NOISE]}
    res = TestClient(app).post("/recommend", json=body)
    assert res.status_code == 200
    assert any("카카오 키가 없어" in n for n in res.json()["notes"])
    bad = {**body, "exclusions": [{"conditions": [{"tool": "nearby_count", "label": "x", "exclude_if": {"op": ">", "value": 1}}]}]}
    assert TestClient(app).post("/recommend", json=bad).status_code == 422
