"""3단계: 이동시간 - 학과 건물 · 시간표 부분, 도보 (설계안 4.1장)."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.data import load_properties
from app.scoring import (
    ClassSlot,
    department_avg_walk,
    missing_destinations,
    time_score,
    timetable_avg_walk,
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


# ---------------------------------------------------------------- 입력·데이터 (criteria 테스트에서 옮김)

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
    assert missing_destinations(P001, ["ECC"]) == []


# ---------------------------------------------------------------- 학과 건물

def test_department_single_and_multiple_buildings():
    assert department_avg_walk(P001, ["공과대학"]) == 6
    assert department_avg_walk(P001, ["학관", "ECC"]) == pytest.approx((9 + 19) / 2)
    assert department_avg_walk(P001, ["학관", "학관"]) == 9  # 중복은 한 번만


def test_department_none_without_building_or_time():
    assert department_avg_walk(P001, []) is None
    assert department_avg_walk(P001, ["공과대학", "없는 건물"]) is None


# ---------------------------------------------------------------- 시간표 (criteria.weekly_commute_min 에서 옮김)

def test_timetable_uses_first_and_last_class_each_day():
    timetable = [
        slot("월", "13:00", "14:15", "ECC"),       # 마지막 수업 19분 (순서가 섞여 들어와도 시각으로 정렬)
        slot("월", "09:00", "10:15", "공과대학"),  # 첫 수업 6분
        slot("월", "11:00", "12:00", "학관"),      # 수업 사이 이동은 제외
        slot("수", "10:30", "11:45", "학관"),      # 하루 한 수업: 9 + 9
    ]
    # 편도 4번: 6, 19, 9, 9
    assert timetable_avg_walk(P001, timetable) == pytest.approx((6 + 19 + 9 + 9) / 4)


def test_timetable_none_without_timetable_or_travel_time():
    assert timetable_avg_walk(P001, []) is None
    assert timetable_avg_walk(P001, [slot("월", "09:00", "10:00", "없는 건물")]) is None


# ---------------------------------------------------------------- 이동시간 내부 비중

def test_part_weights_all_three():
    assert travel_part_weights(["department", "frequent", "timetable"]) == pytest.approx(
        {"department": 0.4, "frequent": 0.4, "timetable": 0.2}
    )


def test_part_weights_redistribute_missing_frequent():
    # 자주 가는 곳 0.4 를 0.4 : 0.2 비율로 → 2/3, 1/3
    assert travel_part_weights(["department", "timetable"]) == pytest.approx({"department": 2 / 3, "timetable": 1 / 3})


def test_part_weights_department_only():
    assert travel_part_weights(["department"]) == {"department": 1.0}


def test_part_weights_none_available():
    assert travel_part_weights([]) == {}


def test_part_weights_from_config():
    cfg = replace(CONFIG, travel_weight_department=0.5, travel_weight_timetable=0.5)
    assert travel_part_weights(["department", "timetable"], cfg) == pytest.approx({"department": 0.5, "timetable": 0.5})


# ---------------------------------------------------------------- 이동시간 점수

def test_department_only_score_equals_time_score():
    result = travel_score(15, department_avg_min=6)
    assert result.score == pytest.approx(time_score(6, 15, 10))
    assert set(result.parts) == {"department"}
    assert result.parts["department"].weight == 1.0


def test_department_and_timetable_weighted_2_to_1():
    result = travel_score(15, department_avg_min=6, timetable_avg_min=25)
    expected = (2 / 3) * time_score(6, 15, 10) + (1 / 3) * time_score(25, 15, 10)
    assert result.score == pytest.approx(expected)
    assert result.parts["timetable"].score == pytest.approx(42.5)
    assert result.parts["timetable"].target_min == 15


def test_frequent_part_uses_its_own_target_and_half_life():
    result = travel_score(15, department_avg_min=6, timetable_avg_min=9, frequent_avg_min=35)
    assert result.parts["frequent"].target_min == 20
    assert result.parts["frequent"].score == pytest.approx(42.5)  # 85 × 2^(−15/15)
    expected = 0.4 * time_score(6, 15, 10) + 0.4 * 42.5 + 0.2 * time_score(9, 15, 10)
    assert result.score == pytest.approx(expected)


def test_desired_commute_changes_score():
    # 같은 26분이라도 희망 통학 시간이 길면 점수가 높다
    assert travel_score(30, department_avg_min=26).score > travel_score(15, department_avg_min=26).score


def test_no_parts_returns_none():
    assert travel_score(15) is None


def test_half_life_from_config():
    cfg = replace(CONFIG, department_half_life_min=5)
    assert travel_score(15, department_avg_min=20, cfg=cfg).score == pytest.approx(42.5)


def test_same_school_different_department_different_top1():
    # (criteria 의 "같은 학교, 다른 패턴" 테스트를 학과 건물 기준으로 옮김)
    def best(buildings):
        return max(PROPS, key=lambda p: (travel_score(15, department_avg_min=department_avg_walk(TRAVEL[p.property_id], buildings)).score, p.property_id))
    assert best(["공과대학"]).property_id == "P007"  # 공과대학 1분
    assert best(["ECC"]).property_id == "P003"       # ECC 6분
