from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_list_properties_returns_10():
    res = client.get("/properties")
    assert res.status_code == 200
    assert len(res.json()) == 10


def test_p001_monthly_rent_is_80():
    res = client.get("/properties/P001")
    assert res.status_code == 200
    body = res.json()
    assert body["monthly_rent_manwon"] == 80
    # 빈 값은 null, 숫자 열은 숫자
    assert body["latitude"] is None
    assert body["listing_checked_at"] is None
    assert isinstance(body["deposit_manwon"], (int, float))
    assert isinstance(body["spot1_walk_min"], (int, float))


def test_unknown_property_returns_404():
    res = client.get("/properties/P999")
    assert res.status_code == 404


def test_cors_allows_vite_dev_server():
    res = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_data_csv_path_relative_to_be_dir(monkeypatch):
    """DATA_CSV_PATH 상대 경로는 실행 위치가 아니라 BE/ 기준이다."""
    from app.config import BE_DIR
    from app.data import DEFAULT_CSV_PATH, _csv_path

    monkeypatch.setenv("DATA_CSV_PATH", "../DB/poc_ewha.csv")
    monkeypatch.chdir(BE_DIR.parent)  # 다른 위치에서 실행해도
    assert _csv_path() == DEFAULT_CSV_PATH.resolve()


def test_walk_table_adds_buildings_to_travel_times(tmp_path, monkeypatch):
    """도보시간 표(WALK_TABLE_PATH)의 건물이 spot1~4 에 더해진다. 표에 없는 매물은 그대로."""
    from app.data import load_properties, load_travel_times, load_walk_table

    f = tmp_path / "walk.csv"
    f.write_text(
        "property_id,building,destination_id,walk_min,route_source,route_mode,checked_at,source_file\n"
        "P001,인문관,EWHA_INMUN,12.5,kakao_walk,BROAD_FIRST,2026-11-08,x\n"
        "NOPE,인문관,EWHA_INMUN,1,kakao_walk,BROAD_FIRST,2026-11-08,x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WALK_TABLE_PATH", str(f))
    load_walk_table.cache_clear()
    times = load_travel_times(load_properties())
    assert times["P001"]["인문관"] == 12.5
    assert times["P001"]["ECC"] == 19  # 기존 spot 값 유지
    assert "인문관" not in times["P002"]
    assert "NOPE" not in times


def test_buildings_endpoint_counts_coverage():
    from fastapi.testclient import TestClient

    from app.main import app

    data = TestClient(app).get("/recommend/buildings").json()
    assert {b["name"] for b in data} == {"ECC", "이화·포스코관", "학관", "공과대학"}
    assert all(b["covered"] == b["total"] == 10 for b in data)
