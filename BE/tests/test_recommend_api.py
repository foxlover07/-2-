from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_recommend_basic_returns_top3():
    res = client.post("/recommend", json={"visits": {"ECC": 3, "학관": 2}, "budget_manwon": None,
                                          "cost_importance": 3, "walk_importance": 4})
    assert res.status_code == 200
    body = res.json()
    assert len(body["top"]) == 3
    assert [p["rank"] for p in body["top"]] == [1, 2, 3]
    assert abs(body["weights"]["cost"] + body["weights"]["walk"] - 1) < 1e-9


def test_recommend_basic_budget_excludes():
    res = client.post("/recommend", json={"visits": {}, "budget_manwon": 60})
    assert res.status_code == 200
    body = res.json()
    assert all(p["monthly_cost_manwon"] <= 60 for p in body["ranked"])
    assert any(e["reason"] == "월 예산 초과" for e in body["excluded"])


def test_recommend_basic_rejects_bad_importance():
    res = client.post("/recommend", json={"cost_importance": 9})
    assert res.status_code == 422


def test_recommend_student_with_timetable():
    lifestyle = {
        "timetable": [
            {"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"},
            {"day": "월", "start": "13:00", "end": "14:15", "building": "공과대학"},
        ],
    }
    res = client.post("/recommend/student", json=lifestyle)
    assert res.status_code == 200
    body = res.json()
    assert body["top"]
    assert "weekly_commute" in body["weights"]


def test_recommend_student_rejects_weights_not_100():
    res = client.post("/recommend/student", json={"weights_percent": {"monthly_cost": 50, "weekly_commute": 30}})
    assert res.status_code == 422


def test_recommend_student_rejects_unknown_area():
    res = client.post("/recommend/student", json={
        "places": [{"area": "강남역", "kind": "work", "visits_per_week": 2}],
    })
    assert res.status_code == 422


def test_top_n_limit():
    assert client.post("/recommend?top_n=11", json={}).status_code == 422
    assert client.post("/recommend/student?top_n=0", json={}).status_code == 422
