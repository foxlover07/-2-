import json
from pathlib import Path

from pipeline.jibsa_adapter import build, housing_cost, location_precision, mark_duplicates, write


def _item(**kw):
    base = {
        "source": "zigbang", "id": "1", "url": "https://example/1", "trade_type": "월세",
        "property_type": "원룸", "deposit_manwon": 1000, "rent_manwon": 60, "manage_cost_manwon": 5,
        "area_m2": 15.0, "floor": "3", "address": "서울특별시 서대문구 대현동 53-33",
    }
    base.update(kw)
    return base


def _make_run(root: Path, name: str, source: str, listings: list[dict], exit_code: int = 0, key: str | None = None):
    """run 폴더 하나에 조회 하나. key 가 없으면 옛(v0) manifest 형식."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    fname = f"{(key or source).replace(':', '__')}.stdout.json"
    (d / fname).write_text(json.dumps({"listings": listings}, ensure_ascii=False), encoding="utf-8")
    run = {"source": source, "exit_code": exit_code, "finished_at": "2026-11-06T20:47:00+09:00", "stdout_file": fname}
    if key:
        run["key"] = key
    mpath = d / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {"runs": []}
    manifest["runs"].append(run)
    mpath.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return d


def test_null_maintenance_is_not_zero():
    assert housing_cost(60, None) is None
    assert housing_cost(70, 9.9) == 79.9
    assert housing_cost(70, 0) == 70  # 원본이 0 이라고 준 경우만 0


def test_location_precision():
    assert location_precision("서울특별시 서대문구 대현동 53-33") == "lot"
    assert location_precision("서울 서대문구 성산로 573") == "lot"
    assert location_precision("서울특별시 서대문구 대현동") == "dong"
    assert location_precision(None) is None


def test_build_keeps_nulls_and_ids(tmp_path):
    d = _make_run(tmp_path, "20260101T000000", "daangn",
                  [_item(source="daangn", id="9", manage_cost_manwon=None, address="서울특별시 마포구 노고산동", in_regions=False)])
    rows = build([d])
    assert len(rows) == 1
    r = rows[0]
    assert r["property_id"] == "daangn:9"
    assert r["maintenance_manwon"] is None
    assert r["monthly_housing_cost_manwon"] is None
    assert r["latitude"] is None and r["longitude"] is None
    assert r["location_precision"] == "dong"
    assert r["source_url"] == "https://example/1"  # 원본 링크 그대로


def test_failed_run_is_skipped_and_latest_success_used(tmp_path):
    a = _make_run(tmp_path, "20260101T000000", "dabang", [_item(source="dabang", id="old")])
    b = _make_run(tmp_path, "20260102T000000", "dabang", [], exit_code=1)
    rows = build([a, b])
    assert [r["source_listing_id"] for r in rows] == ["old"]


def test_later_success_replaces_same_key(tmp_path):
    a = _make_run(tmp_path, "20260101T000000", "dabang", [_item(source="dabang", id="old")])
    b = _make_run(tmp_path, "20260102T000000", "dabang", [_item(source="dabang", id="new")])
    assert [r["source_listing_id"] for r in build([a, b])] == ["new"]


def test_same_listing_in_two_queries_is_one_row(tmp_path):
    d = _make_run(tmp_path, "r", "zigbang", [_item(id="7")], key="zigbang:대현동")
    _make_run(tmp_path, "r", "zigbang", [_item(id="7"), _item(id="8", rent_manwon=99)], key="zigbang:창천동")
    rows = {r["property_id"]: r for r in build([d])}
    assert set(rows) == {"zigbang:7", "zigbang:8"}
    assert rows["zigbang:7"]["found_in"] == "zigbang:대현동|zigbang:창천동"
    assert rows["zigbang:7"]["duplicate_suspect_group"] is None  # 같은 ID 는 중복 의심이 아니라 같은 매물


def test_duplicates_flagged_not_merged():
    rows = [
        {"deposit_manwon": 1000, "monthly_rent_manwon": 60, "area_m2": 15.0, "floor": "3", "duplicate_suspect_group": None},
        {"deposit_manwon": 1000, "monthly_rent_manwon": 60, "area_m2": 15.0, "floor": "3", "duplicate_suspect_group": None},
        {"deposit_manwon": 1000, "monthly_rent_manwon": 60, "area_m2": 15.0, "floor": None, "duplicate_suspect_group": None},
    ]
    mark_duplicates(rows)
    assert rows[0]["duplicate_suspect_group"] == rows[1]["duplicate_suspect_group"] == "D01"
    assert rows[2]["duplicate_suspect_group"] is None
    assert len(rows) == 3


def test_write_csv_empty_for_null(tmp_path):
    d = _make_run(tmp_path / "raw", "20260101T000000", "zigbang", [_item(manage_cost_manwon=None)])
    out = write(build([d]), tmp_path / "staging")
    text = out.read_text(encoding="utf-8")
    assert "zigbang:1" in text
    assert (tmp_path / "staging" / "jibsa_listings_v0.report.json").exists()
