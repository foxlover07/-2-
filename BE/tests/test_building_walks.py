"""고른 건물만 그때 도보시간 계산 (app.building_walks, B 방식). 실제 카카오는 부르지 않는다."""

import csv
import io

import pytest

from app import building_walks as bw
from app import data
from app import frequent_places as fp
from app.scoring import RecommendRequest, recommend
from app.tools.kakao import WalkResult

EWHA = "이화여자대학교"


class FakeWalk:
    def __init__(self, seconds=600, fail=()):
        self.seconds, self.fail, self.calls = seconds, set(fail), 0
        self.by_coord: dict[tuple[float, float], str] = {}

    def walk(self, start, end, route_mode="BROAD_FIRST"):
        self.calls += 1
        pid = self.by_coord[(start.lat, start.lng)]
        if pid in self.fail:
            return WalkResult(status="NO_ROUTE", route_mode=route_mode)
        return WalkResult(status="OK", route_mode=route_mode, distance_m=700, time_s=self.seconds)


@pytest.fixture
def coords(monkeypatch, tmp_path):
    rows = list(csv.DictReader(io.StringIO(data.DEFAULT_CSV_PATH.read_text(encoding="utf-8-sig"))))
    by = {}
    for i, r in enumerate(rows, start=1):
        r["latitude"], r["longitude"] = f"{37.56 + i / 10000:.4f}", "126.9450"
        by[(float(r["latitude"]), 126.945)] = r["property_id"]
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
    path = tmp_path / "poc_coords.csv"
    path.write_text(out.getvalue(), encoding="utf-8")
    monkeypatch.setenv("DATA_CSV_PATH", str(path))
    data.load_properties.cache_clear()
    yield by
    data.load_properties.cache_clear()
    bw.set_services(None)


def use(fake, by, limit=700):
    fake.by_coord = by
    bw.set_services(fp.Services(client=fake, budget=fp.CallBudget(None, limit), cache=fp.WalkCache(None)))


def req(**kw):
    base = dict(university=EWHA, department="직접 선택", department_buildings=["중앙도서관"], grade=1,
                priorities=("travel", "cost"), budget_manwon=90, desired_commute_min=15, desired_area_m2=18)
    return RecommendRequest(**{**base, **kw})


def run(r):
    props = data.load_properties()
    fill = bw.fill_for_request(props, r, data.load_travel_times(props))
    return fill, recommend(props, r, travel=fill.travel)


def test_catalog_names_from_ref():
    names = bw.catalog_names(EWHA)
    assert {"ECC", "학관", "중앙도서관", "이화신세계관", "동창회기념회관"} <= set(names)
    assert "학관 출입구" not in names and all("이화여자대학교" not in n for n in names)
    ku = bw.catalog_names("고려대학교")
    assert {"정경관", "우당교양관", "정운오IT교양관", "SK미래관", "법학관신관"} <= set(ku)


def test_fill_only_budget_passing_listings_then_recommend(coords):
    fake = FakeWalk()
    use(fake, coords)
    r = req()
    fill, res = run(r)
    props = data.load_properties()
    from app.scoring import CONFIG, budget_exclusion_reason, monthly_fixed_cost
    passing = [p for p in props if (c := monthly_fixed_cost(p, CONFIG)) is not None
               and not budget_exclusion_reason(c, r.budget_manwon, CONFIG)]
    assert fake.calls == fill.computed == len(passing) < len(props)
    assert res.ranked and all(x.travel_parts["department"].avg_min == 10.0 for x in res.ranked)
    assert any("지금 카카오 도보 경로로 계산" in n for n in fill.notes())


def test_alias_resolves_before_fill(coords):
    fake = FakeWalk()
    use(fake, coords)
    fill, res = run(req(department_buildings=["동창"]))  # '동창' → 동창회기념회관
    assert fill.buildings == ["동창회기념회관"] and res.ranked


def test_cache_reused(coords):
    fake = FakeWalk()
    use(fake, coords)
    run(req())
    n = fake.calls
    run(req())
    assert fake.calls == n


def test_existing_walk_data_is_not_recomputed(coords):
    fake = FakeWalk()
    use(fake, coords)
    fill, _ = run(req(department_buildings=["ECC"]))
    assert fake.calls == 0 and fill.computed == 0


def test_limits_and_failures_exclude_not_zero(coords, monkeypatch):
    fake = FakeWalk(fail={"P002"})
    use(fake, coords, limit=2)
    fill, res = run(req())
    assert fake.calls == 2 and fill.limited > 0
    ranked = {x.property_id for x in res.ranked}
    assert "P002" not in ranked
    reasons = {e.property_id: e.reason for e in res.excluded}
    assert all("도보시간 정보 없음" in reasons[pid] for pid in reasons if reasons[pid].startswith("도보시간"))
    assert any("계산하지 못해" in n for n in fill.notes())


def test_per_request_cap(coords, monkeypatch):
    monkeypatch.setattr(bw, "MAX_PER_REQUEST", 3)
    fake = FakeWalk()
    use(fake, coords)
    fill, _ = run(req())
    assert fake.calls == 3 and fill.limited > 0


def test_cached_walks_show_up_in_travel_times(coords):
    fake = FakeWalk(seconds=420)
    use(fake, coords)
    run(req())
    travel = data.load_travel_times(data.load_properties())
    assert any(row.get("중앙도서관") == 7.0 for row in travel.values())


class QuotaWalk(FakeWalk):
    """처음 n번만 성공하고 그다음은 카카오 '한도 초과' (2026-11-16 실측 응답)."""

    def __init__(self, ok_calls=2):
        super().__init__()
        self.ok_calls = ok_calls

    def walk(self, start, end, route_mode="BROAD_FIRST"):
        if self.calls >= self.ok_calls:
            self.calls += 1
            return WalkResult(status="HTTP_400", route_mode=route_mode,
                              raw={"errorType": "BadRequest", "code": -10, "msg": "API limit has been exceeded."})
        return super().walk(start, end, route_mode)


def test_kakao_quota_error_is_not_cached_and_stops_calls(coords):
    fake = QuotaWalk(ok_calls=2)
    use(fake, coords)
    fill, _ = run(req())
    assert fill.computed == 2 and fake.calls == 3  # 한도 오류 1번 뒤로는 부르지 않는다
    assert fill.limited > 0 and fill.failed == 0
    svc = bw.services()
    assert svc.budget.remaining() == 0
    assert all(r["status"] == "OK" for r in svc.cache._rows.values())  # 한도 오류는 저장하지 않는다


# ---------------------------------------------------------------- POST /recommend/building-walks (시간표 강의동)

def test_building_walks_endpoint_stats_and_unknown():
    from fastapi.testclient import TestClient

    from app.main import app

    res = TestClient(app).post("/recommend/building-walks", json={
        "university": EWHA, "buildings": ["캠", "학", "동창", "없는건물관"], "property_ids": ["P001"]})
    assert res.status_code == 200
    b = {x["input"]: x for x in res.json()["buildings"]}
    assert b["캠"]["name"] == "ECC" and b["캠"]["covered"] == b["캠"]["total"] == 10 and b["캠"]["min_walk_min"] is not None
    assert b["동창"]["name"] == "동창회기념회관" and b["동창"]["covered"] == 0 and b["동창"]["can_compute"]
    assert b["없는건물관"]["name"] is None
    walks = res.json()["walks"]["P001"]
    assert walks["ECC"] is not None and walks["동창회기념회관"] is None
    assert any("없는건물관" in n for n in res.json()["notes"])


def test_building_walks_endpoint_compute(coords):
    from fastapi.testclient import TestClient

    from app.main import app

    fake = FakeWalk(seconds=300)
    use(fake, coords)
    res = TestClient(app).post("/recommend/building-walks", json={
        "university": EWHA, "buildings": ["동창"], "property_ids": ["P001"], "compute": True}).json()
    assert res["computed"] == 10 and fake.calls == 10
    assert res["buildings"][0]["covered"] == 10 and res["buildings"][0]["median_walk_min"] == 5.0
    assert res["walks"]["P001"]["동창회기념회관"] == 5.0
