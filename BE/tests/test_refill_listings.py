"""새 매물로 다시 채우기: 겹치는 매물 빼기 + 같은 주소 경로 재사용 (pipeline.refill_listings.prepare)."""

import csv
import json

from pipeline import refill_listings as rl


def _w(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _listing(lid, addr, dep="1000", rent="50", area="20", floor="3", ptype="원룸"):
    return {"property_id": f"zigbang:{lid}", "source": "zigbang", "source_listing_id": lid, "address": addr,
            "property_type": ptype, "deposit_manwon": dep, "monthly_rent_manwon": rent, "area_m2": area, "floor": floor}


def test_prepare_drops_overlaps_and_reuses_routes(tmp_path, monkeypatch, capsys):
    db = tmp_path / "DB"
    monkeypatch.setattr(rl, "DB", db)
    ref = tmp_path / "ref.csv"
    _w(ref, [{"destination_id": d, "point_role": "building_poi", "csv_spot_name": d} for d in ("D1", "D2")])
    monkeypatch.setattr(rl.universities, "ref_path", lambda univ: ref)
    # 기존 후보: 1 (주소 A)
    _w(db / "staging" / "x_candidates.csv", [{"property_id": "zigbang:1", "source": "zigbang", "source_listing_id": "1",
                                               "address": "A", "deposit_manwon": "1000", "monthly_rent_manwon": "50",
                                               "area_m2": "20"}])
    new = [
        _listing("1", "A"),                       # 이미 후보
        _listing("2", "A"),                       # ID 다르지만 주소·가격·면적·층 같음 → 같은 매물로 보고 뺌
        _listing("3", "A", rent="55"),            # 같은 건물 다른 방 → 넣고, 주소 A 경로 재사용
        _listing("8", "A", rent="55", floor="4"),  # 3 과 가격·면적 같지만 층이 둘 다 알려져 있고 다름 → 다른 방
        _listing("9", "A", rent="55", floor=""),   # 층 미확인 → 같은 매물로 보고 뺌
        _listing("4", "B"),                       # 새 주소 → 경로 새로 필요
        _listing("5", "B"),                       # 4 와 같은 매물 → 뺌
        _listing("6", "C", ptype="빌라"),          # 유형 제외
        _listing("7", "C", dep="5000"),           # 보증금 초과
    ]
    _w(db / "staging" / "x_listings_v2.csv", new)
    route = {"property_id": "zigbang:1", "destination_id": "D1", "walk_min": "5", "status": "OK", "checked_at": "t1"}
    _w(db / "routes" / "routes_x.csv", [route, {**route, "destination_id": "D2", "walk_min": "7"},
                                        {**route, "destination_id": "OTHER_UNIV", "walk_min": "9"}])  # 다른 대학 건물은 안 옮김

    rl.prepare("ewha", "staging/x_listings_v2.csv", "staging/x_candidates.csv")
    rep = json.loads(capsys.readouterr().out)
    assert rep["new"] == 3 and rep["route_reused"] == 2 and rep["need_route_addresses"] == 1
    assert rep["dropped"]["이미 후보에 있음"] == 1
    assert rep["dropped"]["같은 매물로 보임 (주소·보증금·월세·면적 같고 층 같음·미확인)"] == 3
    reuse = list(csv.DictReader((db / rep["files"]["reuse_routes"]).open(encoding="utf-8")))
    assert {(r["property_id"], r["destination_id"], r["walk_min"]) for r in reuse} == {
        (pid, d, m) for pid in ("zigbang:3", "zigbang:8") for d, m in (("D1", "5"), ("D2", "7"))}
    need = list(csv.DictReader((db / rep["files"]["needroute"]).open(encoding="utf-8-sig")))
    assert [r["source_listing_id"] for r in need] == ["4"]
