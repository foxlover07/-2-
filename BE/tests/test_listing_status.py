"""거래 가능 재확인 (pipeline.check_listing_status → app.listing_status → app.data)."""

from __future__ import annotations

import csv
import json
import subprocess

from fastapi.testclient import TestClient

from app import data
from app.listing_status import load_status
from pipeline import check_listing_status as cls

COLS = ["property_id", "university", "property_type", "address", "latitude", "longitude", "deposit_manwon",
        "monthly_rent_manwon", "maintenance_manwon", "area_m2", "source_url", "listing_checked_at",
        "spot1_name", "spot1_walk_min", "spot2_name", "spot2_walk_min", "spot3_name", "spot3_walk_min",
        "spot4_name", "spot4_walk_min", "monthly_housing_cost_manwon", "source", "source_listing_id", "listing_status"]


def _listings(path):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for pid, lid in (("A", "1"), ("B", "2"), ("C", "3")):
            w.writerow({"property_id": pid, "university": "이화여자대학교", "property_type": "원룸", "address": "x",
                        "deposit_manwon": 500, "monthly_rent_manwon": 50, "maintenance_manwon": 5, "area_m2": 20,
                        "monthly_housing_cost_manwon": 55, "source": "zigbang", "source_listing_id": lid,
                        "listing_status": "advertised_unverified"})


def _status(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cls.FIELDS)
        w.writeheader()
        for lid, st, up, at in rows:
            w.writerow({"source": "zigbang", "source_listing_id": lid, "status": st, "upstream_status": up, "checked_at": at})


def test_unavailable_hidden_from_lists_but_detail_shows_status(tmp_path, monkeypatch):
    lst, s1, s2 = tmp_path / "l.csv", tmp_path / "listing_status_1.csv", tmp_path / "listing_status_2.csv"
    _listings(lst)
    _status(s1, [("1", "unavailable", "close", "2026-11-15T10:00:00+09:00"), ("2", "active", None, "2026-11-15T10:00:00+09:00")])
    # 나중 확인: 2 는 실패(판정 보류) → 이전 active 유지, 3 은 거래 종료
    _status(s2, [("2", "error", None, "2026-11-16T10:00:00+09:00"), ("3", "unavailable", "cancel", "2026-11-16T10:00:00+09:00")])
    monkeypatch.setenv("DATA_CSV_PATH", str(lst))
    monkeypatch.setenv("LISTING_STATUS_PATH", f"{s1},{s2}")
    data.load_properties.cache_clear()
    try:
        assert [p.property_id for p in data.load_properties()] == ["B"]
        a = data.get_property("A")
        assert a.listing_status == "unavailable" and a.listing_status_detail == "close"
        assert data.get_property("B").listing_status == "active"
        from app.main import app
        c = TestClient(app)
        assert [p["property_id"] for p in c.get("/properties").json()] == ["B"]
        assert c.get("/properties/C").json()["listing_status_detail"] == "cancel"
    finally:
        monkeypatch.setenv("LISTING_STATUS_PATH", "off")
        data.load_properties.cache_clear()


def test_status_off_keeps_everything(tmp_path, monkeypatch):
    lst = tmp_path / "l.csv"
    _listings(lst)
    monkeypatch.setenv("DATA_CSV_PATH", str(lst))
    data.load_properties.cache_clear()
    assert load_status() == {} and len(data.load_properties()) == 3


def _fake_run(code, out="", err=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=out, stderr=err)


def test_check_one_parses_active_unavailable_and_errors(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0, json.dumps({"id": "1", "deposit_manwon": 1000, "rent_manwon": 93,
                                                                      "manage_cost_manwon": None})))
    row, _ = cls.check_one("jibsa", "zigbang", "1")
    assert row["status"] == "active" and row["monthly_rent_manwon"] == 93 and row["maintenance_manwon"] is None
    gone = {"error": {"code": "LISTING_UNAVAILABLE", "listing_status": "close"}}
    monkeypatch.setattr(subprocess, "run", _fake_run(1, err=json.dumps(gone)))
    row, _ = cls.check_one("jibsa", "zigbang", "1")
    assert row["status"] == "unavailable" and row["upstream_status"] == "close"
    monkeypatch.setattr(subprocess, "run", _fake_run(1, err=json.dumps({"error": {"code": "RATE_LIMITED"}})))
    row, _ = cls.check_one("jibsa", "zigbang", "1")
    assert row["status"] == "error" and row["error_code"] == "RATE_LIMITED"   # 판정 보류: 빼지 않는다


def test_prune_removes_unavailable_rows_in_place(tmp_path, monkeypatch, capsys):
    from pipeline import prune_unavailable

    lst, s1 = tmp_path / "cands.csv", tmp_path / "listing_status_1.csv"
    _listings(lst)
    _status(s1, [("1", "unavailable", "close", "2026-11-15T10:00:00+09:00"), ("2", "active", None, "2026-11-15T10:00:00+09:00"),
                 ("3", "error", None, "2026-11-15T10:00:00+09:00")])
    monkeypatch.setenv("DATA_CSV_PATH", str(lst))
    monkeypatch.setenv("LISTING_STATUS_PATH", str(s1))
    load_status.cache_clear()
    try:
        monkeypatch.setattr("sys.argv", ["x", "--dry-run"])
        prune_unavailable.main()
        assert len(list(csv.DictReader(lst.open(encoding="utf-8-sig")))) == 3   # dry-run 은 고치지 않는다
        monkeypatch.setattr("sys.argv", ["x"])
        prune_unavailable.main()
    finally:
        monkeypatch.setenv("LISTING_STATUS_PATH", "off")
        load_status.cache_clear()
    rows = list(csv.DictReader(lst.open(encoding="utf-8-sig")))   # 원래 파일에서 행이 지워진다
    assert [(r["property_id"], r["listing_status"]) for r in rows] == [("B", "active"), ("C", "advertised_unverified")]
    backs = list((tmp_path / "pruned").glob("cands_removed_*.csv"))
    assert len(backs) == 1 and [r["property_id"] for r in csv.DictReader(backs[0].open(encoding="utf-8-sig"))] == ["A"]
    rep = json.loads(backs[0].with_suffix(".report.json").read_text(encoding="utf-8"))
    assert rep["removed"] == 1 and rep["removed_rows"][0]["upstream_status"] == "close" and rep["unchecked_kept"] == 1
