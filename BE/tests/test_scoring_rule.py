"""3장 공통 규칙 (기준 50점, 좋은 쪽 단위당 +5, 나쁜 쪽 단위당 −10) + 교환비 + 6장 설정값."""

from dataclasses import replace

import pytest

from app.scoring import area_score, cost_score, standard_score, travel_minutes_score
from app.scoring_config import CONFIG, DEPARTMENT_BUILDINGS, EWHA

CSV_BUILDINGS = {"ECC", "이화·포스코관", "학관", "공과대학"}


# ---------------------------------------------------------------- 3.4장 예시표 (희망 통학 15분, 유지비 상한 75만 원, 희망 면적 20㎡)

@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(0, 57.5), (5, 55), (15, 50), (25, 40), (35, 30), (65, 0), (90, 0)],
)
def test_design_table_travel(minutes, expected):
    assert travel_minutes_score(minutes, 15) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("cost", "expected"),
    [(10, 100), (25, 100), (45, 80), (65, 60), (75, 50), (80, 40), (85, 30)],
)
def test_design_table_cost(cost, expected):
    assert cost_score(cost, 75) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("area", "like", "neutral", "dislike"),
    [
        (8, 0, 0, 0),
        (10, 0, 0, 0),
        (16, 30, 30, 30),
        (18, 40, 40, 40),
        (20, 50, 50, 50),
        (24, 60, 50, 45),
        (30, 75, 50, 37.5),
        (40, 100, 50, 25),
    ],
)
def test_design_table_area(area, like, neutral, dislike):
    assert area_score(area, 20, "like") == pytest.approx(like)
    assert area_score(area, 20, "neutral") == pytest.approx(neutral)
    assert area_score(area, 20, "dislike") == pytest.approx(dislike)


# ---------------------------------------------------------------- 공통 규칙의 성질

def test_meeting_the_target_is_50_for_every_factor():
    assert travel_minutes_score(23, 23) == 50
    assert cost_score(61, 61) == 50
    for pref in ("like", "neutral", "dislike"):
        assert area_score(17, 17, pref) == 50


def test_exchange_rate_same_units_same_points():
    # 편도 10분 = 월 5만 원 = 2㎡: 기준보다 한 단위 나쁘면 어느 요소든 −10점, 한 단위 좋으면 +5점
    assert travel_minutes_score(25, 15) == cost_score(80, 75) == area_score(18, 20) == 40
    assert travel_minutes_score(5, 15) == cost_score(70, 75) == area_score(22, 20, "like") == 55


def test_bad_side_is_twice_as_steep():
    assert 50 - travel_minutes_score(20, 15) == 2 * (travel_minutes_score(10, 15) - 50)


def test_keeps_rising_inside_target():
    # 희망 시간 안에서도 가까울수록 꾸준히 오른다 (v2 의 평평한 구간 없음)
    scores = [travel_minutes_score(t, 40) for t in range(0, 41)]
    assert all(a > b for a, b in zip(scores, scores[1:]))


def test_clamped_to_0_100():
    assert travel_minutes_score(500, 15) == 0
    assert cost_score(0, 200) == 100


def test_invalid_inputs():
    with pytest.raises(ValueError):
        travel_minutes_score(-1, 15)
    with pytest.raises(ValueError):
        standard_score(1, 1, 0, higher_is_better=True)


def test_rule_comes_from_config():
    cfg = replace(CONFIG, base_score=60, good_slope=2, bad_slope=4, unit_travel_min=5)
    assert travel_minutes_score(15, 15, cfg) == 60
    assert travel_minutes_score(10, 15, cfg) == 62
    assert travel_minutes_score(20, 15, cfg) == 56


# ---------------------------------------------------------------- 6장 설정값

def test_config_initial_values_match_section_6():
    c = CONFIG
    assert (c.monthly_budget_slack_manwon, c.deposit_budget_slack_ratio) == (10, 0.2)
    assert (c.base_score, c.good_slope, c.bad_slope) == (50, 5, 10)
    assert (c.unit_travel_min, c.unit_cost_manwon, c.unit_area_m2) == (10, 5, 2)
    assert (c.larger_area_like_slope, c.larger_area_neutral_slope, c.larger_area_dislike_slope) == (5, 0, -2.5)
    assert (c.travel_weight_department, c.travel_weight_frequent, c.travel_weight_timetable) == (0.4, 0.4, 0.2)
    assert (c.weight_rank1, c.weight_rank2, c.weight_rank3) == (0.55, 0.30, 0.15)
    assert (c.not_recommended_top_k, c.not_recommended_max) == (3, 3)
    assert c.kakao_call_limit == 500


def test_weights_sum_to_one():
    c = CONFIG
    assert c.travel_weight_department + c.travel_weight_frequent + c.travel_weight_timetable == pytest.approx(1)
    assert c.weight_rank1 + c.weight_rank2 + c.weight_rank3 == pytest.approx(1)


def test_department_example_table_is_ewha_only_and_uses_csv_buildings():
    assert set(DEPARTMENT_BUILDINGS) == {EWHA}
    for buildings in DEPARTMENT_BUILDINGS[EWHA].values():
        assert buildings and set(buildings) <= CSV_BUILDINGS
