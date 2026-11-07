"""5단계: POST /recommend/v2 (설계안 v2, 비교용) 입력·출력."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BODY = {
    "budget_manwon": 75,
    "deposit_budget_manwon": 1000,
    "department": "컴퓨터공학전공",
    "grade": 2,
    "desired_commute_min": 15,
    "timetable": [
        {"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"},
        {"day": "월", "start": "13:00", "end": "14:15", "building": "공과대학"},
    ],
    "priorities": ["travel", "cost"],
}


def post(**overrides):
    return client.post("/recommend/v2", json={**BODY, **overrides})


def test_recommend_returns_v2_shape():
    res = post()
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"priorities", "weights", "ranked", "not_recommended", "excluded", "assumptions", "notes"}
    assert body["priorities"] == ["travel", "cost", "transit"]
    assert abs(sum(body["weights"].values()) - 1) < 1e-9
    assert body["weights"]["transit"] == 0

    first = body["ranked"][0]
    assert first["rank"] == 1
    assert set(first["factor_scores"]) == {"travel", "transit", "cost"}
    assert first["factor_scores"]["transit"] is None
    assert set(first["travel_parts"]) == {"department", "timetable"}
    assert isinstance(first["over_budget"], bool)


def test_budget_filter_reasons_in_response():
    body = post().json()
    reasons = {e["property_id"]: e["reason"] for e in body["excluded"]}
    assert reasons["P001"] == "예산보다 월 12만 원 비싸서 제외"
    # 보증금 1000 × 1.2 = 1200 < P003 보증금 2000
    assert reasons["P003"] == "보증금이 예산보다 1000만 원 많아서 제외"


def test_optional_inputs_can_be_omitted():
    body = {k: v for k, v in BODY.items() if k not in ("deposit_budget_manwon", "timetable")}
    res = client.post("/recommend/v2", json=body)
    assert res.status_code == 200
    assert set(res.json()["ranked"][0]["travel_parts"]) == {"department"}


def test_transit_priority_note():
    body = post(priorities=["transit", "travel"]).json()
    assert any("선택한 순위가 반영되지 않았습니다" in n for n in body["notes"])


def test_not_recommended_and_assumptions_present():
    body = post(deposit_budget_manwon=None).json()
    assert body["not_recommended"]
    nr = body["not_recommended"][0]
    assert {"property_id", "best_rank_elsewhere", "top_in", "weak", "strong", "message"} <= set(nr)
    a = body["assumptions"]
    assert a["time_score"]["department"]["T"] == 15
    assert a["config"]["weight_rank1"] == 0.55


def test_department_buildings_override():
    body = post(department="없는학과", department_buildings=["학관"]).json()
    assert body["assumptions"]["department_buildings"] == ["학관"]


def test_validation_errors_are_422():
    assert post(priorities=["cost", "cost"]).status_code == 422
    assert post(priorities=["travel"]).status_code == 422
    assert post(budget_manwon=0).status_code == 422
    assert post(department="없는학과").status_code == 422
    assert post(timetable=[{"day": "월", "start": "9:00", "end": "10:00", "building": "ECC"}]).status_code == 422
    required = {k: v for k, v in BODY.items() if k != "budget_manwon"}
    assert client.post("/recommend/v2", json=required).status_code == 422


def test_unknown_building_is_422_with_message():
    res = post(department_buildings=["포관"])
    assert res.status_code == 422
    assert "포관" in res.json()["detail"]


def test_v1_body_and_student_endpoint_are_gone():
    # 중요도 1~5 · 방문 횟수(v1) 모양은 더 이상 받지 않는다
    assert client.post("/recommend/v2", json={"visits": {"ECC": 3}, "cost_importance": 3}).status_code == 422
    assert client.post("/recommend/student", json={}).status_code == 404
