"""POST /recommend (추천 시스템 통합안) 입력·출력."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BODY = {
    "university": "이화여자대학교",
    "department": "컴퓨터공학전공",
    "department_buildings": None,
    "grade": 2,
    "priorities": ["travel", "cost"],
    "budget_manwon": 75,
    "deposit_budget_manwon": 1000,
    "desired_commute_min": 15,
    "desired_area_m2": 20,
    "larger_area": "neutral",
    "timetable": [{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"}],
    "exclusions": [],
}


def post(**overrides):
    return client.post("/recommend", json={**BODY, **overrides})


def test_recommend_shape():
    res = post()
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "priorities", "weights", "ranked", "not_recommended", "excluded", "exclusion_counts", "assumptions", "notes",
    }
    assert body["priorities"] == ["travel", "cost", "area"]
    assert abs(sum(body["weights"].values()) - 1) < 1e-9
    first = body["ranked"][0]
    assert set(first["factor_scores"]) == {"travel", "cost", "area"}
    assert set(first["travel_parts"]) == {"department", "timetable"}
    assert set(first["vs_target"]) == {"travel_min", "cost_manwon", "area_m2"}
    assert isinstance(first["over_budget"], bool) and first["area_m2"] > 0


def test_filter_reasons_in_response():
    body = post().json()
    reasons = {e["property_id"]: e["reason"] for e in body["excluded"]}
    assert reasons["P001"] == "유지비가 상한보다 월 12만 원 많아서 제외"
    assert reasons["P003"] == "보증금이 상한보다 1000만 원 많아서 제외"


def test_optional_inputs_can_be_omitted():
    body = {k: v for k, v in BODY.items() if k not in ("deposit_budget_manwon", "timetable", "exclusions", "larger_area", "university", "department_buildings")}
    res = client.post("/recommend", json=body)
    assert res.status_code == 200
    assert res.json()["assumptions"]["larger_area"] == "neutral"


def test_validation_errors_are_422():
    assert post(priorities=["cost", "cost"]).status_code == 422
    assert post(priorities=["travel", "transit"]).status_code == 422
    assert post(priorities=["travel"]).status_code == 422
    assert post(budget_manwon=0).status_code == 422
    assert post(desired_area_m2=None).status_code == 422
    assert post(larger_area="maybe").status_code == 422
    assert post(department="없는학과").status_code == 422
    assert post(timetable=[{"day": "월", "start": "9:00", "end": "10:00", "building": "ECC"}]).status_code == 422


def test_unknown_building_is_422_with_message():
    res = post(department_buildings=["중앙도서관"])
    assert res.status_code == 422
    assert "중앙도서관" in res.json()["detail"]


def test_old_endpoints_are_gone():
    assert client.post("/recommend/v2", json={}).status_code in (404, 405)
    assert client.get("/recommend/categories").status_code in (404, 405)
    # v3 모양(categories, upkeep_max_manwon)은 받지 않는다
    assert client.post("/recommend", json={"upkeep_max_manwon": 75, "categories": ["weekly_commute"]}).status_code == 422


def test_buildings_endpoint_kept():
    data = client.get("/recommend/buildings").json()
    assert {b["name"] for b in data} == {"ECC", "이화·포스코관", "학관", "공과대학"}
    assert client.get("/recommend/buildings", params={"university": "고려대학교"}).json() == []
