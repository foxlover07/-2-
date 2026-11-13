"""3.3장 이동 시간: 학과 건물 · 시간표 부분 (도보), 각 부분에 공통 규칙 적용 후 비중대로 합침."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.buildings import resolve
from app.data import load_properties
from app.scoring import (
    ClassSlot,
    RecommendRequest,
    department_avg_walk,
    missing_destinations,
    recommend,
    timetable_avg_walk,
    travel_minutes_score,
    travel_part_weights,
    travel_score,
    travel_times_from_csv,
)
from app.scoring_config import CONFIG

PROPS = list(load_properties())
TRAVEL = travel_times_from_csv(PROPS)

# P001: ECC 19, 이화·포스코관 14, 학관 9, 공과대학 6
P001 = TRAVEL["P001"]


def slot(day, start, end, building):
    return ClassSlot(day=day, start=start, end=end, building=building)


# ---------------------------------------------------------------- 입력·데이터

def test_time_format_is_checked():
    with pytest.raises(ValidationError):
        slot("월", "9:00", "10:15", "ECC")
    with pytest.raises(ValidationError):
        slot("월", "09:00", "24:00", "ECC")
    with pytest.raises(ValidationError):
        slot("월요일", "09:00", "10:00", "ECC")


def test_travel_times_from_csv_reads_four_buildings():
    assert P001 == {"ECC": 19, "이화·포스코관": 14, "학관": 9, "공과대학": 6}
    assert len(TRAVEL) == len(PROPS)


def test_missing_destinations():
    assert missing_destinations(P001, ["ECC", "중앙도서관", "ECC", "본관"]) == ["중앙도서관", "본관"]


# ---------------------------------------------------------------- 편도 평균

def test_department_single_and_multiple_buildings():
    assert department_avg_walk(P001, ["공과대학"]) == 6
    assert department_avg_walk(P001, ["학관", "ECC"]) == pytest.approx((9 + 19) / 2)
    assert department_avg_walk(P001, ["학관", "학관"]) == 9
    assert department_avg_walk(P001, []) is None
    assert department_avg_walk(P001, ["공과대학", "없는 건물"]) is None


def test_timetable_uses_first_and_last_class_each_day():
    timetable = [
        slot("월", "13:00", "14:15", "ECC"),       # 마지막 수업 19분 (순서가 섞여 들어와도 시각으로 정렬)
        slot("월", "09:00", "10:15", "공과대학"),  # 첫 수업 6분
        slot("월", "11:00", "12:00", "학관"),      # 수업 사이 이동은 제외
        slot("수", "10:30", "11:45", "학관"),      # 하루 한 수업: 9 + 9
    ]
    assert timetable_avg_walk(P001, timetable) == pytest.approx((6 + 19 + 9 + 9) / 4)
    assert timetable_avg_walk(P001, []) is None
    assert timetable_avg_walk(P001, [slot("월", "09:00", "10:00", "없는 건물")]) is None


# ---------------------------------------------------------------- 내부 비중

def test_part_weights():
    assert travel_part_weights(["department", "frequent", "timetable"]) == pytest.approx(
        {"department": 0.4, "frequent": 0.4, "timetable": 0.2}
    )
    # 자주 가는 곳(데이터 준비 중) 0.4 를 0.4 : 0.2 비율로 → 2/3, 1/3
    assert travel_part_weights(["department", "timetable"]) == pytest.approx({"department": 2 / 3, "timetable": 1 / 3})
    assert travel_part_weights(["department"]) == {"department": 1.0}
    assert travel_part_weights([]) == {}


def test_part_weights_from_config():
    cfg = replace(CONFIG, travel_weight_department=0.5, travel_weight_timetable=0.5)
    assert travel_part_weights(["department", "timetable"], cfg) == pytest.approx({"department": 0.5, "timetable": 0.5})


# ---------------------------------------------------------------- 이동 시간 점수

def test_department_only_score_uses_common_rule():
    r = travel_score(15, department_avg_min=5)
    assert r.score == pytest.approx(55)                  # 희망보다 10분 가까움 → +5
    assert r.parts["department"].target_min == 15


def test_department_and_timetable_weighted_2_to_1():
    r = travel_score(15, department_avg_min=5, timetable_avg_min=25)
    assert r.parts["timetable"].score == pytest.approx(40)
    assert r.score == pytest.approx((2 / 3) * 55 + (1 / 3) * 40)
    assert r.avg_min == pytest.approx((2 / 3) * 5 + (1 / 3) * 25)


def test_each_part_scored_before_combining():
    # 부분마다 규칙을 먼저 적용한 다음 합친다 (평균 시간을 한 번에 점수화하지 않는다)
    r = travel_score(15, department_avg_min=5, timetable_avg_min=35)
    assert r.score == pytest.approx((2 / 3) * travel_minutes_score(5, 15) + (1 / 3) * travel_minutes_score(35, 15))
    assert r.score != pytest.approx(travel_minutes_score(r.avg_min, 15))


def test_desired_commute_changes_score():
    assert travel_score(30, department_avg_min=26).score > travel_score(15, department_avg_min=26).score


def test_no_parts_returns_none():
    assert travel_score(15) is None


# ---------------------------------------------------------------- 건물 이름 정규화·줄임말 (v3 테스트에서 옮김)

def test_building_resolve_normalizes_and_aliases():
    known = ["ECC", "이화·포스코관", "학관", "공과대학", "조형예술관 A동"]
    assert resolve("이화포스코관", known) == "이화·포스코관"
    assert resolve("이화여자대학교 학관", known) == "학관"
    assert resolve("ecc", known) == "ECC"
    assert resolve("조형예술관A동", known) == "조형예술관 A동"
    assert resolve("포관", known) == "이화·포스코관"
    assert resolve("공대", known) == "공과대학"
    assert resolve("종과", known) == "종과"        # 모호한 이름은 바꾸지 않음


def test_recommend_reads_aliases_and_notes_them():
    req = RecommendRequest(
        department="컴퓨터공학전공", grade=2, priorities=("travel", "cost"), budget_manwon=100,
        desired_commute_min=15, desired_area_m2=20,
        timetable=[slot("월", "09:00", "10:15", "포관"), slot("수", "09:00", "10:15", "공대")],
    )
    r = recommend(PROPS, req)
    assert r.ranked and set(r.ranked[0].travel_parts) == {"department", "timetable"}
    assert any("'포관'을(를) '이화·포스코관'" in n for n in r.notes)
    assert {"from": "공대", "to": "공과대학"} in r.assumptions["renamed_buildings"]
