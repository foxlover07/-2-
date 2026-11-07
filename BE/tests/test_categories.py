"""추천 v3: 카테고리 3개 순서 선택 (2026-11-08 팀 결정)."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.categories import (
    CATEGORIES,
    RecommendV3Request,
    filter_reason,
    first_class,
    gap_home,
    recommend_v3,
    weekly_commute,
    weekly_commute_minutes,
)
from app.data import load_properties
from app.main import app
from app.scoring import ClassSlot
from app.scoring_config import CONFIG

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}
TIMES = {"ECC": 10.0, "공과대학": 5.0, "학관": 20.0}


def slot(day, start, end, building):
    return ClassSlot(day=day, start=start, end=end, building=building)


TT = [
    slot("월", "09:00", "10:15", "공과대학"),
    slot("월", "13:00", "14:15", "ECC"),     # 월 공강 165분
    slot("수", "10:30", "11:45", "학관"),
    slot("수", "12:00", "13:15", "학관"),    # 수 공강 15분
]


def req(**overrides) -> RecommendV3Request:
    base = dict(upkeep_max_manwon=75, department="컴퓨터공학전공", grade=2, categories=["weekly_commute", "gap_home", "first_class"])
    return RecommendV3Request(**{**base, **overrides})


# ---------------------------------------------------------------- 카탈로그

def test_nine_categories_and_ready_ones():
    assert [c.no for c in CATEGORIES.values()] == list(range(1, 10))
    assert {k for k, c in CATEGORIES.items() if c.ready} == {"weekly_commute", "gap_home", "first_class"}
    assert CATEGORIES["late_return"].label == "심야 귀가 동선"  # 안전 점수 단정 금지 → 이름 변경


# ---------------------------------------------------------------- 1. 주간 총 통학 시간

def test_weekly_commute_sums_first_and_last_each_day():
    # 월: 공대 5 + ECC 10, 수: 학관 20 + 학관 20 → 55분
    assert weekly_commute_minutes(TIMES, TT, ["공과대학"]) == (55, "timetable")


def test_weekly_commute_falls_back_to_department_buildings():
    # 시간표 없음: 학과 건물 편도 5분 × 2 × 주 5일 = 50분
    assert weekly_commute_minutes(TIMES, [], ["공과대학"]) == (50, "department_fallback")


def test_weekly_commute_score_and_label():
    v = weekly_commute(TIMES, TT, ["공과대학"], target=150)
    assert v.label == "주 55분"
    assert v.score == pytest.approx(100 - 15 * 55 / 150)


def test_weekly_commute_unknown_building_is_none():
    assert weekly_commute({"ECC": 1}, TT, ["공과대학"], target=150).score is None


# ---------------------------------------------------------------- 2. 공강 귀가

def test_gap_home_counts_possible_gaps():
    # 월 공강 165분 ≥ 공대 5 + 30 + ECC 10 → 가능, 수 공강 15분 < 20 + 30 + 20 → 불가
    v = gap_home(TIMES, TT, rest_min=30)
    assert (v.detail["possible"], v.detail["total"]) == (1, 2)
    assert v.score == 50
    assert v.label == "1 / 2회"


def test_gap_home_without_timetable_or_gaps_is_none():
    assert gap_home(TIMES, [], 30).score is None
    assert gap_home(TIMES, [slot("월", "09:00", "10:00", "ECC")], 30).detail["reason"] == "공강 없음"


# ---------------------------------------------------------------- 5. 1교시 부담

def test_first_class_uses_days_starting_by_nine():
    # 월만 9:00 수업 (공대 5분 + 여유 10분 = 15분) → 8:45 출발, 수는 10:30 이라 제외
    v = first_class(TIMES, TT, buffer_min=10)
    assert v.label == "8:45 출발"
    assert v.detail["days"] == 1
    assert v.score == pytest.approx(100 - 15 * 15 / 20)


def test_first_class_none_when_no_early_class():
    assert first_class(TIMES, [slot("화", "10:30", "11:45", "ECC")], 10).detail["reason"] == "1교시 없음"


# ---------------------------------------------------------------- 필터 (예산은 점수에 안 씀)

def test_filter_upkeep_and_deposit_caps():
    p003 = BY_ID["P003"]  # 월 55, 보증금 2000
    assert filter_reason(p003, upkeep_max=55, deposit_max=None) is None
    assert filter_reason(p003, upkeep_max=50, deposit_max=None) == "유지비가 상한보다 월 5만 원 많아서 제외"
    assert filter_reason(p003, upkeep_max=60, deposit_max=1500) == "보증금이 상한보다 500만 원 많아서 제외"


def test_filter_missing_maintenance_is_excluded():
    p = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    assert filter_reason(p, upkeep_max=100, deposit_max=None) == "월세 또는 관리비 정보 없음"


# ---------------------------------------------------------------- 종합

def test_weights_follow_rank_and_skip_not_ready_categories():
    r = recommend_v3(PROPS, req(categories=["amenities", "weekly_commute", "first_class"], timetable=TT_POC()))
    # 편의시설(1순위, 준비 중)은 빠지고 0.30 : 0.15 = 2 : 1
    assert r.weights["amenities"] == 0
    assert r.weights["weekly_commute"] == pytest.approx(2 / 3)
    assert r.weights["first_class"] == pytest.approx(1 / 3)
    assert any("생활 편의시설" in n and "데이터 준비 중" in n for n in r.notes)


def TT_POC():
    return [slot("월", "09:00", "10:15", "공과대학"), slot("월", "13:00", "14:15", "ECC"), slot("수", "09:00", "10:15", "학관")]


def test_all_filtered_listings_are_ranked_and_top_k_marked():
    r = recommend_v3(PROPS, req(timetable=TT_POC()))
    passed = [p for p in PROPS if filter_reason(p, 75, None) is None]
    assert len(r.ranked) == len(passed)  # 상위 K 만이 아니라 필터 통과 매물 전체
    assert [x.top for x in r.ranked[:4]] == [True, True, True, False]
    scores = [x.score for x in r.ranked]
    assert scores == sorted(scores, reverse=True)


def test_no_timetable_uses_department_and_skips_timetable_only_categories():
    r = recommend_v3(PROPS, req())
    assert r.weights["gap_home"] == 0 and r.weights["first_class"] == 0
    assert r.weights["weekly_commute"] == pytest.approx(1)
    assert any("학과 건물" in n for n in r.notes)


def test_score_does_not_depend_on_budget():
    a = recommend_v3(PROPS, req(upkeep_max_manwon=100, timetable=TT_POC()))
    b = recommend_v3(PROPS, req(upkeep_max_manwon=200, timetable=TT_POC()))
    sa = {x.property_id: x.score for x in a.ranked}
    sb = {x.property_id: x.score for x in b.ranked}
    assert all(sa[k] == sb[k] for k in sa)


def test_nothing_computable_sorts_by_upkeep():
    r = recommend_v3(PROPS, req(categories=["amenities"]))
    assert all(x.score is None and not x.top for x in r.ranked)
    upkeeps = [x.upkeep_manwon for x in r.ranked]
    assert upkeeps == sorted(upkeeps)


def test_request_validation():
    with pytest.raises(ValidationError):
        req(categories=["gap_home", "gap_home"])
    with pytest.raises(ValidationError):
        req(categories=["gap_home", "first_class", "weekly_commute", "uphill"])
    with pytest.raises(ValidationError):
        req(categories=[])
    with pytest.raises(ValidationError):
        req(department="없는학과")


# ---------------------------------------------------------------- API

client = TestClient(app)


def test_api_recommend_v3_shape():
    body = {"upkeep_max_manwon": 80, "deposit_max_manwon": 1000, "department": "컴퓨터공학전공", "grade": 2,
            "categories": ["weekly_commute", "gap_home", "first_class"],
            "answers": {"weekly_commute_target_min": 120, "gap_rest_min": 30, "first_class_buffer_min": 10},
            "timetable": [{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"},
                          {"day": "월", "start": "13:00", "end": "14:15", "building": "공과대학"}]}
    res = client.post("/recommend", json=body)
    assert res.status_code == 200
    data = res.json()
    assert set(data) == {"categories", "weights", "top_k", "ranked", "excluded", "notes", "assumptions"}
    first = data["ranked"][0]
    assert first["top"] is True
    assert set(first["category_values"]) == {"weekly_commute", "gap_home", "first_class"}
    assert abs(sum(data["weights"].values()) - 1) < 1e-9


def test_api_categories_catalog():
    data = client.get("/recommend/categories").json()
    assert len(data) == 9
    assert data[0]["key"] == "weekly_commute" and data[0]["ready"] is True


def test_api_unknown_building_422():
    body = {"upkeep_max_manwon": 80, "department": "x", "department_buildings": ["없는관"], "grade": 1, "categories": ["weekly_commute"]}
    assert client.post("/recommend", json=body).status_code == 422


def test_weights_constant_from_config():
    assert (CONFIG.weight_rank1, CONFIG.weight_rank2, CONFIG.weight_rank3) == (0.55, 0.30, 0.15)


# ---------------------------------------------------------------- 건물 이름 정규화·줄임말

def test_building_resolve_normalizes_and_aliases():
    from app.buildings import resolve

    known = ["ECC", "이화·포스코관", "학관", "공과대학", "조형예술관 A동"]
    assert resolve("이화포스코관", known) == "이화·포스코관"       # 가운뎃점 없음
    assert resolve("이화여자대학교 학관", known) == "학관"          # 접두어
    assert resolve("ecc", known) == "ECC"                            # 대소문자
    assert resolve("조형예술관A동", known) == "조형예술관 A동"      # 띄어쓰기
    assert resolve("포관", known) == "이화·포스코관"                 # 줄임말
    assert resolve("공대", known) == "공과대학"
    assert resolve("종과", known) == "종과"                          # 모호한 이름은 바꾸지 않음
    assert resolve("생환관", known) == "생환관"                      # 표준 목록에 없는 건물로는 바꾸지 않음


def test_recommend_reads_aliases_and_notes_them():
    tt = [slot("월", "09:00", "10:15", "포관"), slot("수", "09:00", "10:15", "공대")]
    r = recommend_v3(PROPS, req(timetable=tt))
    assert r.ranked and r.weights["first_class"] > 0
    assert any("'포관'을(를) '이화·포스코관'" in n for n in r.notes)
    assert {"from": "공대", "to": "공과대학"} in r.assumptions["renamed_buildings"]
