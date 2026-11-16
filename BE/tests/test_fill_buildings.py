"""pipeline.fill_buildings — 빈 (주소 × 건물) 만 계획, 하루 상한에서 멈추고 다음 실행에서 이어 감, 실패는 0분으로 채우지 않음.

카카오는 부르지 않는다 (가짜 클라이언트). 데이터는 임시 폴더에 만든다.
"""

import csv
from types import SimpleNamespace

import pytest


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def env(tmp_path, monkeypatch):
    from pipeline import build_walk_table, fill_buildings as fb, refill_listings, universities

    db = tmp_path / "DB"
    ref = db / "ref" / "ewha_buildings.csv"
    blds = [{"destination_id": d, "university": "이화여자대학교", "csv_spot_name": n, "point_role": "building_poi",
             "building_type": "강의동", "kakao_place_name": n, "kakao_place_id": "", "latitude": "37.5620",
             "longitude": "126.9470", "coord_source": "", "checked_at": "", "entrance_verified": "false", "note": ""}
            for d, n in (("E1", "ECC"), ("E2", "학관"), ("E3", "인문관"))]
    _write(ref, blds)
    cands = [{"property_id": "P1", "address": "서울 서대문구 대현동 1", "latitude": "37.5600", "longitude": "126.9450",
              "spot1_name": "", "spot1_walk_min": "", "spot2_name": "", "spot2_walk_min": "", "spot3_name": "",
              "spot3_walk_min": "", "spot4_name": "", "spot4_walk_min": ""},
             {"property_id": "P2", "address": "서울 서대문구 대현동 1", "latitude": "37.5600", "longitude": "126.9450",
              "spot1_name": "", "spot1_walk_min": "", "spot2_name": "", "spot2_walk_min": "", "spot3_name": "",
              "spot3_walk_min": "", "spot4_name": "", "spot4_walk_min": ""},
             {"property_id": "P3", "address": "서울 마포구 노고산동 9", "latitude": "37.5610", "longitude": "126.9460",
              "spot1_name": "", "spot1_walk_min": "", "spot2_name": "", "spot2_walk_min": "", "spot3_name": "",
              "spot3_walk_min": "", "spot4_name": "", "spot4_walk_min": ""}]
    _write(db / "staging" / "cands.csv", cands)
    # P1·P2(같은 주소)는 E1 이 이미 있음 → 그 주소는 E2·E3 만, P3 주소는 E1~E3
    _write(db / "staging" / "walk.csv", [
        {"property_id": "P1", "building": "ECC", "destination_id": "E1", "walk_min": "5", "route_source": "kakao_walk",
         "route_mode": "BROAD_FIRST", "checked_at": "2026-11-10", "source_file": "x"},
        {"property_id": "P2", "building": "ECC", "destination_id": "E1", "walk_min": "5", "route_source": "kakao_walk",
         "route_mode": "BROAD_FIRST", "checked_at": "2026-11-10", "source_file": "x"}])
    (db / "routes").mkdir()
    for mod in (fb, refill_listings, build_walk_table):
        monkeypatch.setattr(mod, "DB", db)
    monkeypatch.setattr(fb, "FILL_DIR", db / "staging" / "fill")
    monkeypatch.setattr(universities, "ref_path", lambda u: ref)
    monkeypatch.setattr(build_walk_table, "building_names", lambda: {"E1": "ECC", "E2": "학관", "E3": "인문관"})

    calls = []

    class FakeKakao:
        def __init__(self, key):
            pass

        def search_address(self, addr, exact=True):
            return {"q": addr}

        def walk(self, start, end, route_mode=None):
            calls.append((start, end))
            fail = len(calls) == 2  # 두 번째 호출은 경로 없음
            return SimpleNamespace(ok=not fail, status="NO_ROUTE" if fail else "OK", time_s=None if fail else 600,
                                   distance_m=None if fail else 700, raw={"n": len(calls)})

        def close(self):
            pass

    import app.config
    import app.tools.kakao as kakao
    monkeypatch.setattr(kakao, "KakaoClient", FakeKakao)
    monkeypatch.setattr(kakao, "single_exact_match",
                        lambda res, addr: (kakao.LatLng(lat=37.56, lng=126.945), None))
    monkeypatch.setattr(app.config, "get_settings", lambda: SimpleNamespace(kakao_rest_api_key="k"))
    return SimpleNamespace(db=db, fb=fb, calls=calls)


def test_plan_run_resume_and_merge(env):
    fb, db = env.fb, env.db
    fb.plan("ewha", "staging/cands.csv", "staging/walk.csv", 2.0)
    plan_rel = "staging/fill/" + next((db / "staging" / "fill").glob("ewha_plan_*.csv")).name
    plan = _rows(db / plan_rel)
    assert len(plan) == 5  # 주소 A: E2·E3, 주소 B: E1~E3 (이미 있는 E1 은 빼고, 같은 주소 매물은 한 번만)

    fb.run("ewha", plan_rel, max_calls=3)  # 하루 상한에서 멈춤
    assert len(env.calls) == 3
    fb.run("ewha", plan_rel, max_calls=10)  # 다음 날: 남은 2건만 (실패한 것도 다시 부르지 않음)
    assert len(env.calls) == 5

    rows = [r for p in sorted((db / "routes").glob("routes_fill_ewha_*.csv")) for r in _rows(p)]
    failed = [r for r in rows if r["status"] != "OK"]
    assert failed and all(r["walk_min"] == "" for r in failed)  # 실패는 0분으로 채우지 않음

    fb.merge("ewha", "staging/cands.csv", "staging/walk.csv")
    walk = _rows(db / "staging" / "walk.csv")
    pairs = {(r["property_id"], r["destination_id"]) for r in walk}
    assert ("P1", "E1") in pairs and len([r for r in walk if (r["property_id"], r["destination_id"]) == ("P1", "E1")]) == 1
    ok_pairs = {(r["property_id"], r["destination_id"]) for r in rows if r["status"] == "OK"}
    assert ok_pairs <= pairs  # 성공한 경로는 모두 표에 들어감
    assert not {(r["property_id"], r["destination_id"]) for r in failed} & (pairs - {("P1", "E1"), ("P2", "E1")})
    assert list((db / "staging" / "fill").glob("walk_before_*.csv"))  # 덧붙이기 전 백업
