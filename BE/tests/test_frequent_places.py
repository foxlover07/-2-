"""자주 가는 곳 (app.frequent_places + 챗봇 연결). 실제 카카오는 부르지 않는다 — FakeKakao.

PoC CSV 에는 좌표가 없어서 테스트용 좌표를 붙인 사본을 쓴다 (테스트 전용, 실제 위치 아님).
"""

import csv
import io
import uuid

import pytest

from app import data
from app import frequent_places as fp
from app.agent import tools
from app.agent.graph import build_graph
from app.agent.llm import ScriptedLLM
from app.agent.schemas import AgentProfile, FrequentPlaceIn, ProfileChanges, TurnExtraction
from app.scoring import RecommendRequest, recommend
from app.tools.kakao import WalkResult

EWHA = "이화여자대학교"
FULL = dict(university="이대", budget_manwon=90, department="공대", department_buildings=["공대"],
            priorities=["travel", "cost"], desired_commute_min=15, desired_area_m2=18)
SINCHON = {"id": "111", "place_name": "신촌역 2호선", "road_address_name": "서울 서대문구 신촌로 지하 90",
           "x": "126.9368", "y": "37.5552", "distance": "900"}


class FakeKakao:
    """장소 검색은 query → 문서 목록, 도보는 매물 ID → 초 (없으면 경로 실패)."""

    def __init__(self, near=None, far=None, walk_s=None):
        self.near, self.far, self.walk_s = near or {}, far or {}, walk_s or {}
        self.calls = {"near": 0, "far": 0, "walk": 0}
        self._by_coord: dict[tuple[float, float], str] = {}

    def search_places_near(self, **kw):
        self.calls["near"] += 1
        return {"documents": self.near.get(kw["query"], [])}

    def search_keyword(self, query, size=5, page=1, rect=None):
        self.calls["far"] += 1
        return {"documents": self.far.get(query, [])}

    def walk(self, start, end, route_mode="BROAD_FIRST"):
        self.calls["walk"] += 1
        pid = self._by_coord[(start.lat, start.lng)]
        if pid not in self.walk_s:
            return WalkResult(status="NO_ROUTE", route_mode=route_mode)
        return WalkResult(status="OK", route_mode=route_mode, distance_m=self.walk_s[pid], time_s=self.walk_s[pid])


@pytest.fixture
def coords_csv(monkeypatch, tmp_path):
    """PoC 10건에 테스트 좌표를 붙인다 (P001 → 37.5601, ...)."""
    rows = list(csv.DictReader(io.StringIO(data.DEFAULT_CSV_PATH.read_text(encoding="utf-8-sig"))))
    coords = {}
    for i, r in enumerate(rows, start=1):
        r["latitude"], r["longitude"] = f"{37.56 + i / 10000:.4f}", "126.9450"
        coords[(float(r["latitude"]), 126.945)] = r["property_id"]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
    path = tmp_path / "poc_coords.csv"
    path.write_text(out.getvalue(), encoding="utf-8")
    monkeypatch.setenv("DATA_CSV_PATH", str(path))
    data.load_properties.cache_clear()
    yield coords
    data.load_properties.cache_clear()


def use(fake: FakeKakao | None, coords=None, limit=200):
    if fake is not None and coords:
        fake._by_coord = coords
    fp.set_services(fp.Services(client=fake, budget=fp.CallBudget(None, limit), cache=fp.WalkCache(None)))


@pytest.fixture(autouse=True)
def _reset_services():
    yield
    fp.set_services(None)


def base_profile() -> AgentProfile:
    p, notes = tools.apply_changes(AgentProfile(), ProfileChanges(**FULL))
    assert p.university == EWHA, notes
    return p


def with_place(p: AgentProfile, query="신촌역", visits=3) -> tuple[AgentProfile, list[str]]:
    return tools.apply_changes(p, ProfileChanges(frequent_places=[FrequentPlaceIn(query=query, name="알바", visits_per_week=visits)]))


# ---------------------------------------------------------------- 장소 찾기

def test_place_found_is_applied_with_name_and_address(coords_csv):
    use(FakeKakao(near={"신촌역": [SINCHON]}), coords_csv)
    p, notes = with_place(base_profile())
    assert [f.place_name for f in p.frequent_places] == ["신촌역 2호선"]
    assert p.frequent_places[0].visits_per_week == 3
    assert any("신촌역 2호선" in n and "신촌로" in n and "주 3회" in n for n in notes)


def test_ambiguous_place_is_not_applied(coords_csv):
    docs = [dict(SINCHON, id="1", place_name="GS25 신촌점"), dict(SINCHON, id="2", place_name="CU 이대점")]
    use(FakeKakao(near={"편의점 알바": docs}), coords_csv)
    p, notes = with_place(base_profile(), query="편의점 알바")
    assert p.frequent_places == []
    assert any("여럿" in n and "GS25 신촌점" in n for n in notes)


def test_place_outside_radius_is_not_applied(coords_csv):
    use(FakeKakao(far={"강남역": [dict(SINCHON, place_name="강남역 2호선")]}), coords_csv)
    p, notes = with_place(base_profile(), query="강남역")
    assert p.frequent_places == []
    assert any("km 밖" in n for n in notes)


def test_no_kakao_key_says_unavailable(coords_csv):
    use(None)
    p, notes = with_place(base_profile())
    assert p.frequent_places == [] and any("카카오 키" in n for n in notes)


def test_remove_frequent_place(coords_csv):
    use(FakeKakao(near={"신촌역": [SINCHON]}), coords_csv)
    p, _ = with_place(base_profile())
    p2, notes = tools.apply_changes(p, ProfileChanges(remove_frequent_places=["알바"]))
    assert p2.frequent_places == [] and any("뺐습니다" in n for n in notes)


# ---------------------------------------------------------------- 도보 계산·순위

def _engine(**kw):
    props = data.load_properties()
    req = RecommendRequest(university=EWHA, department="공대", department_buildings=["공과대학"], grade=1,
                           priorities=("travel", "cost"), budget_manwon=90, desired_commute_min=15, desired_area_m2=18)
    return recommend(props, req, travel=data.load_travel_times(props), **kw)


def test_without_places_matches_engine(coords_csv):
    use(FakeKakao(), coords_csv)
    rec = tools.run_recommendation(base_profile())
    d = _engine()
    assert rec.ranks == {r.property_id: (r.rank, r.score) for r in d.ranked}
    assert rec.frequent is None


def test_rerank_only_inside_top_n(coords_csv, monkeypatch):
    base = _engine()
    ids = [r.property_id for r in base.ranked]
    assert len(ids) >= 6
    monkeypatch.setattr(fp, "TOP_N", 4)
    # 1차 4위 매물이 장소에서 가장 가깝고, 5위 이하 매물은 더 가까워도 계산 범위 밖
    walk = {pid: 1800 for pid in ids[:4]}
    walk[ids[3]] = 60
    walk[ids[4]] = 30
    fake = FakeKakao(near={"신촌역": [SINCHON]}, walk_s=walk)
    use(fake, coords_csv)
    p, _ = with_place(base_profile())
    rec = tools.run_recommendation(p)
    order = sorted(rec.ranks, key=lambda k: rec.ranks[k][0])
    assert set(order[:4]) == set(ids[:4])            # 앞 4자리는 1차 상위 4개 그대로
    assert order[0] == ids[3]                         # 그 안에서 장소가 가까운 매물이 올라온다
    assert order[4:] == ids[4:]                       # 나머지 순서는 그대로
    assert fake.calls["walk"] == 4                    # 상위 4개만 계산
    assert rec.frequent and rec.frequent.top_n == 4 and rec.frequent.computed == 4
    assert rec.top[0].frequent_walk_min == {"알바": 1.0}


def test_route_failure_is_unknown_not_zero(coords_csv, monkeypatch):
    monkeypatch.setattr(fp, "TOP_N", 3)
    ids = [r.property_id for r in _engine().ranked]
    fake = FakeKakao(near={"신촌역": [SINCHON]}, walk_s={ids[0]: 600, ids[1]: 600})  # ids[2] 경로 실패
    use(fake, coords_csv)
    p, _ = with_place(base_profile())
    rec = tools.run_recommendation(p)
    assert rec.frequent.unknown == 1
    walk = {t.property_id: t.frequent_walk_min["알바"] for t in rec.top}
    assert walk[ids[2]] is None
    assert any("미확인" in n for n in rec.notes)


def test_cache_reused_on_second_run(coords_csv, monkeypatch):
    monkeypatch.setattr(fp, "TOP_N", 3)
    ids = [r.property_id for r in _engine().ranked]
    fake = FakeKakao(near={"신촌역": [SINCHON]}, walk_s={pid: 300 for pid in ids})
    use(fake, coords_csv)
    p, _ = with_place(base_profile())
    tools.run_recommendation(p)
    tools.run_recommendation(p)
    assert fake.calls["walk"] == 3


def test_daily_limit_stops_calls(coords_csv, monkeypatch):
    monkeypatch.setattr(fp, "TOP_N", 5)
    ids = [r.property_id for r in _engine().ranked]
    fake = FakeKakao(near={"신촌역": [SINCHON]}, walk_s={pid: 300 for pid in ids})
    use(fake, coords_csv, limit=3)  # 검색 1 + 도보 2
    p, _ = with_place(base_profile())
    rec = tools.run_recommendation(p)
    assert fake.calls["walk"] == 2
    assert rec.frequent.limited == 3 and rec.frequent.unknown == 3
    assert any("한도" in n for n in rec.notes)


def test_missing_property_coords_is_unknown(monkeypatch):
    """PoC CSV 그대로(좌표 없음) → 카카오를 부르지 않고 미확인."""
    fake = FakeKakao(near={"신촌역": [SINCHON]}, walk_s={})
    use(fake)
    p, _ = with_place(base_profile())
    rec = tools.run_recommendation(p)
    assert fake.calls["walk"] == 0 and rec.frequent.unknown == rec.frequent.top_n


# ---------------------------------------------------------------- 챗봇 흐름

def test_chat_saves_place_and_what_if_does_not(coords_csv):
    ids = [r.property_id for r in _engine().ranked]
    use(FakeKakao(near={"신촌역": [SINCHON]}, walk_s={pid: 300 for pid in ids}), coords_csv)
    place = [FrequentPlaceIn(query="신촌역", name="알바", visits_per_week=3)]
    llm = ScriptedLLM([
        TurnExtraction(intent="recommend", changes=ProfileChanges(**FULL)),
        TurnExtraction(intent="update", changes=ProfileChanges(frequent_places=place)),
        TurnExtraction(intent="what_if", changes=ProfileChanges(remove_frequent_places=["*"])),
    ])
    g = build_graph(lambda: llm)
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    g.invoke({"user_message": "추천", "session_id": "t"}, config=cfg)
    s = g.invoke({"user_message": "알바가 신촌역 근처라 주 3번 가", "session_id": "t"}, config=cfg)
    assert s["profile_changed"] is True
    assert [f["place_name"] for f in s["current_profile"]["frequent_places"]] == ["신촌역 2호선"]
    assert s["last_recommendation"]["frequent"]["places"][0]["visits_per_week"] == 3
    s = g.invoke({"user_message": "알바 없으면?", "session_id": "t"}, config=cfg)
    assert s["current_profile"]["frequent_places"]  # 가정은 저장하지 않는다
    assert s["scenario_profile"]["frequent_places"] == []
