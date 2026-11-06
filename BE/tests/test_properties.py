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
