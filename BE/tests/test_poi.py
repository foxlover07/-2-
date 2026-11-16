"""교통·공원 주변 거리 (app.poi) · 헬스장 업종 · 통합 데이터셋 매물 상세 붙이기."""

from __future__ import annotations

import csv

from app import amenities as am
from app.models import Property
from app.place_search import local_category
from app.poi import Poi, nearby_pois
from pipeline.build_integrated import listing_detail

LAT, LON = 37.5600, 126.9400
D100 = 100 / 111_000   # 위도 100m


def prop(**kw) -> Property:
    base = dict(property_id="P1", university="이화여자대학교", property_type="원룸", address="서울 서대문구 대현동 1",
                latitude=LAT, longitude=LON, deposit_manwon=500, monthly_rent_manwon=50, maintenance_manwon=5, area_m2=20,
                source_url=None, listing_checked_at=None, monthly_housing_cost_manwon=55,
                **{f"spot{i}_{k}": None for i in range(1, 5) for k in ("name", "walk_min")})
    base.update(kw)
    return Property(**base)


def test_subway_lines_merge_but_far_same_name_is_separate():
    pois = (
        Poi("subway", "신촌", "2호선", LAT + 2 * D100, LON, None, "2021-01-01"),
        Poi("subway", "신촌", "9호선(가정)", LAT + 2.5 * D100, LON, None, "2021-01-01"),   # 50m 옆 → 같은 역
        Poi("subway", "신촌", "경의중앙선", LAT + 8 * D100, LON, None, "2021-01-01"),    # 600m 떨어짐 → 다른 역
        Poi("subway", "이대", "2호선", LAT + 4 * D100, LON, None, "2021-01-01"),
    )
    r = nearby_pois(prop(), pois)
    sub = next(k for k in r.kinds if k.kind == "subway")
    assert sub.nearest.name == "신촌" and sub.nearest.distance_m == 200
    assert sub.nearest.detail == "2호선 · 9호선(가정)"
    assert sub.counts == {"500": 2, "1000": 3}   # 신촌(2·9) + 이대 / + 신촌(경의중앙)


def test_bus_and_park_counts_and_missing_is_none():
    pois = (
        Poi("bus_stop", "A", "", LAT + D100, LON, None, ""),
        Poi("bus_stop", "B", "", LAT + 4 * D100, LON, None, ""),
        Poi("park", "어린이공원", "어린이공원", LAT + 6 * D100, LON, 1200.0, ""),
    )
    r = nearby_pois(prop(), pois)
    k = {s.kind: s for s in r.kinds}
    assert k["bus_stop"].nearest.distance_m == 100 and k["bus_stop"].counts == {"300": 1, "500": 2}
    assert k["park"].nearest.area_m2 == 1200.0 and k["park"].counts["500"] == 0
    assert k["subway"].nearest is None and k["subway"].counts == {"500": 0, "1000": 0}   # 없으면 None (0m 아님)


def test_no_data_or_no_coord():
    assert nearby_pois(prop(), ()).available is False
    assert nearby_pois(prop(latitude=None, longitude=None), (Poi("park", "x", "", LAT, LON, None, ""),)).available is False


def test_gym_keyword_maps_to_store_category():
    assert local_category("헬스장") == "gym" and local_category("헬스") == "gym"
    assert "gym" in am.CATEGORY_INFO


def _write_stores(path, rows):
    cols = ["category", "name", "branch", "rdnm_adr", "lno_adr", "lat", "lon", "stdr_ym"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for cat, name in rows:
            w.writerow({"category": cat, "name": name, "branch": "", "rdnm_adr": "", "lno_adr": "a", "lat": LAT, "lon": LON,
                        "stdr_ym": "202609"})


def test_store_files_merge_latest_per_category(tmp_path, monkeypatch):
    old, new = tmp_path / "stores_1.csv", tmp_path / "stores_2.csv"
    _write_stores(old, [("convenience", "옛편의점"), ("gym", "옛헬스")])
    _write_stores(new, [("gym", "새헬스")])
    monkeypatch.setattr(am, "_store_paths", lambda: [old, new])
    am.load_stores.cache_clear()
    try:
        names = {(s.category, s.name) for s in am.load_stores()}
    finally:
        am.load_stores.cache_clear()
    assert names == {("convenience", "옛편의점"), ("gym", "새헬스")}   # 헬스장은 최근 파일만, 편의점은 옛 파일 유지


def test_listing_detail_prefers_same_checked_at_then_latest():
    key = ("고려대학교", "zigbang", "1")
    details = {key: [{"floor": "2", "listing_checked_at": "t1"}, {"floor": "3", "listing_checked_at": "t2"}]}
    base = {"university": "고려대학교", "source": "zigbang", "source_listing_id": "1"}
    assert listing_detail(details, {**base, "listing_checked_at": "t1"})["floor"] == "2"
    assert listing_detail(details, {**base, "listing_checked_at": "t9"})["floor"] == "3"
    assert listing_detail(details, {**base, "source_listing_id": "x"}) is None
