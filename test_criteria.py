import pytest
from pydantic import ValidationError

from app.criteria import (
    ClassSlot,
    FrequentPlace,
    Lifestyle,
    cosine_similarity,
    frequent_place_min,
    gap_home_count,
    higher_is_better,
    is_late_night,
    late_return_min,
    merge_travel_times,
    preference_match,
    recommend_for_student,
    travel_times_from_csv,
    weekly_commute_min,
)
from app.data import load_properties

PROPS = list(load_properties())
TRAVEL = travel_times_from_csv(PROPS)

# P001: ECC 19, 이화·포스코관 14, 학관 9, 공과대학 6
P001 = TRAVEL["P001"]


def slot(day, start, end, building):
    return ClassSlot(day=day, start=start, end=end, building=building)


# ---------------------------------------------------------------- 입력 검증

def test_time_format_is_checked():
    with pytest.raises(ValidationError):
        slot("월", "9:00", "10:15", "ECC")
    with pytest.raises(ValidationError):
        FrequentPlace(area="신촌역", visits_per_week=2, ends_at="25:00")


def test_area_must_be_selected_from_list():
    with pytest.raises(ValidationError):
        FrequentPlace(area="강남역", visits_per_week=1)


def test_weights_percent_must_sum_to_100_and_use_known_keys():
    with pytest.raises(ValidationError):
        Lifestyle(weights_percent={"monthly_cost": 50, "weekly_commute": 40})
    with pytest.raises(ValidationError):
        Lifestyle(weights_percent={"monthly_cost": 50, "unknown": 50})
    Lifestyle(weights_percent={"monthly_cost": 50, "weekly_commute": 50})


# ---------------------------------------------------------------- 1. 주간 총 통학 시간

def test_weekly_commute_uses_first_and_last_class_each_day():
    timetable = [
        slot("월", "09:00", "10:15", "공과대학"),  # 첫 수업 6분
        slot("월", "13:00", "14:15", "ECC"),       # 마지막 수업 19분
        slot("수", "10:30", "11:45", "학관"),      # 하루 한 수업: 9 + 9
    ]
    assert weekly_commute_min(P001, timetable) == 6 + 19 + 9 + 9


def test_weekly_commute_none_without_timetable_or_travel_time():
    assert weekly_commute_min(P001, []) is None
    assert weekly_commute_min(P001, [slot("월", "09:00", "10:00", "없는 건물")]) is None


# ---------------------------------------------------------------- 2. 공강 귀가

def test_gap_home_count_needs_round_trip_plus_buffer():
    timetable = [
        slot("화", "09:00", "10:00", "공과대학"),
        slot("화", "11:00", "12:00", "공과대학"),  # 공강 60분 ≥ 6 + 6 + 30 → 가능
        slot("화", "12:30", "13:30", "ECC"),       # 공강 30분 < 6 + 19 + 30 → 불가
    ]
    assert gap_home_count(P001, timetable) == 1
    assert gap_home_count(P001, timetable, buffer_min=0) == 2


def test_gap_home_count_none_without_gaps():
    assert gap_home_count(P001, [slot("화", "09:00", "10:00", "학관")]) is None


# ---------------------------------------------------------------- 3. 자주 가는 곳 (알바 포함)

def test_frequent_place_counts_one_way_from_campus_and_round_trip_otherwise():
    times = {"신촌역": 10, "이대역": 5}
    places = [
        FrequentPlace(area="신촌역", kind="work", visits_per_week=3, from_campus=True),  # 3 × 10
        FrequentPlace(area="이대역", kind="gym", visits_per_week=4),                      # 4 × 2 × 5
    ]
    assert frequent_place_min(times, places) == 30 + 40


def test_frequent_place_none_when_no_place_or_unknown_time():
    assert frequent_place_min({}, []) is None
    assert frequent_place_min({}, [FrequentPlace(area="홍대입구역", visits_per_week=1)]) is None


# ---------------------------------------------------------------- 4. 심야 귀가

def test_late_night_includes_after_midnight():
    assert is_late_night("22:00")
    assert is_late_night("01:30")
    assert not is_late_night("21:59")


def test_late_return_counts_only_late_schedules():
    times = {"홍대입구역": 12, "광화문·시청": 8}
    places = [
        FrequentPlace(area="홍대입구역", kind="work", visits_per_week=2, ends_at="23:00"),
        FrequentPlace(area="광화문·시청", kind="study", visits_per_week=5, ends_at="18:00"),
    ]
    assert late_return_min(times, places) == 2 * 12
    assert late_return_min(times, places[1:]) is None


# ---------------------------------------------------------------- 5. 콘텐츠 기반

def test_cosine_similarity_basic():
    assert cosine_similarity({"a": 1}, {"a": 1}) == pytest.approx(1.0)
    assert cosine_similarity({"a": 1}, {"b": 1}) == pytest.approx(0.0)
    assert cosine_similarity({}, {"a": 1}) is None


def test_preference_match_only_looks_at_preferred_features():
    prefs = {"quiet": 1.0, "safe_route": 1.0}
    good = {"quiet": 1.0, "safe_route": 0.9, "full_option": 0.0}
    bad = {"quiet": 0.0, "safe_route": 0.1, "full_option": 1.0}
    assert preference_match(good, prefs) > preference_match(bad, prefs)
    assert preference_match(good, {}) is None


def test_higher_is_better():
    assert higher_is_better({"a": 1, "b": 3}) == {"a": 0.0, "b": 1.0}
    assert higher_is_better({"a": 2, "b": 2}) == {"a": 1.0, "b": 1.0}


# ---------------------------------------------------------------- 통합: 같은 학교, 다른 추천

ENGINEERING = Lifestyle(
    timetable=[slot(d, "09:00", "12:00", "공과대학") for d in ("월", "화", "수", "목")],
    weights_percent={"weekly_commute": 80, "monthly_cost": 20},
)
ECC_STUDENT = Lifestyle(
    timetable=[slot(d, "09:00", "12:00", "ECC") for d in ("월", "화", "수", "목")],
    weights_percent={"weekly_commute": 80, "monthly_cost": 20},
)


def test_same_school_different_patterns_get_different_top1():
    eng = recommend_for_student(PROPS, ENGINEERING)
    ecc = recommend_for_student(PROPS, ECC_STUDENT)
    assert eng.top[0].property_id == "P007"  # 공과대학 1분
    assert ecc.top[0].property_id == "P003"  # ECC 6분, 월 55만원
    assert eng.top[0].property_id != ecc.top[0].property_id


def test_percent_becomes_weights():
    result = recommend_for_student(PROPS, ENGINEERING)
    assert result.weights == {"monthly_cost": pytest.approx(0.2), "weekly_commute": pytest.approx(0.8)}


def test_zero_percent_criterion_is_not_computed():
    lifestyle = ENGINEERING.model_copy(update={"weights_percent": {"weekly_commute": 100, "monthly_cost": 0}})
    result = recommend_for_student(PROPS, lifestyle)
    assert result.weights == {"weekly_commute": pytest.approx(1.0)}
    assert result.top[0].property_id == "P007"


def test_percent_of_criterion_without_input_is_redistributed():
    # 공강 20% 를 줬지만 공강이 없는 시간표 → 남은 80% 를 비율대로 (주거비 25%, 통학 75%)
    lifestyle = ENGINEERING.model_copy(
        update={"weights_percent": {"monthly_cost": 20, "weekly_commute": 60, "gap_home": 20}}
    )
    result = recommend_for_student(PROPS, lifestyle)
    assert result.weights == {"monthly_cost": pytest.approx(0.25), "weekly_commute": pytest.approx(0.75)}
    assert any("20%" in n for n in result.notes)
    assert any("공강" in n for n in result.notes)


def test_no_percent_means_equal_split():
    lifestyle = ENGINEERING.model_copy(update={"weights_percent": {}})
    result = recommend_for_student(PROPS, lifestyle)
    assert result.weights == {"monthly_cost": pytest.approx(0.5), "weekly_commute": pytest.approx(0.5)}


def test_contributions_sum_to_score_and_reasons_are_given():
    result = recommend_for_student(PROPS, ENGINEERING)
    for s in result.ranked:
        assert sum(s.contributions.values()) == pytest.approx(s.score, abs=1e-3)
    top = result.top[0]
    assert top.strengths[0].criterion == "weekly_commute"  # 가중치가 큰 장점부터


def test_place_without_travel_time_excludes_property_with_reason():
    lifestyle = ENGINEERING.model_copy(
        update={
            "places": [FrequentPlace(area="신촌역", kind="work", visits_per_week=3, from_campus=True)],
            "weights_percent": {"weekly_commute": 50, "monthly_cost": 20, "frequent_place": 30},
        }
    )
    travel = merge_travel_times(TRAVEL, {"P001": {"신촌역": 10}, "P002": {"신촌역": 12}})
    result = recommend_for_student(PROPS, lifestyle, travel=travel)
    assert {s.property_id for s in result.ranked} == {"P001", "P002"}
    assert all("자주 가는 곳" in e.reason for e in result.excluded)
    assert len(result.excluded) == len(PROPS) - 2


def test_budget_only_marks_over_budget():
    lifestyle = ENGINEERING.model_copy(update={"budget_manwon": 70})
    result = recommend_for_student(PROPS, lifestyle)
    assert len(result.ranked) == len(PROPS)
    p004 = next(s for s in result.ranked if s.property_id == "P004")
    assert p004.over_budget is True
