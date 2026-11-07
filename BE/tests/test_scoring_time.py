"""1단계: 설정 파일 + 시간 점수 함수 score(t, T, h) (설계안 3장, 8장)."""

from dataclasses import replace

import pytest

from app.scoring import time_score
from app.scoring_config import CONFIG, DEPARTMENT_BUILDINGS

CSV_BUILDINGS = {"ECC", "이화·포스코관", "학관", "공과대학"}


# 설계안 3장 표: T = 15분, h = 10분 (표 값은 정수로 반올림돼 있으므로 ±0.5)
@pytest.mark.parametrize(
    ("t", "expected"),
    [(0, 100), (5, 95), (10, 90), (15, 85), (20, 60), (25, 43), (30, 30), (45, 11), (60, 4)],
)
def test_design_table_t15_h10(t, expected):
    assert time_score(t, 15, 10) == pytest.approx(expected, abs=0.5)


def test_key_points_exact():
    assert time_score(0, 15, 10) == 100
    assert time_score(15, 15, 10) == 85
    assert time_score(25, 15, 10) == pytest.approx(42.5)  # 반감기 1번 → 85 / 2


def test_target_is_always_85_for_any_target():
    for target in (5, 10, 15, 30):
        assert time_score(target, target, 10) == pytest.approx(85)


def test_one_point_per_minute_inside_target_when_t15():
    assert time_score(1, 15, 10) - time_score(2, 15, 10) == pytest.approx(1)


def test_strictly_decreasing_and_never_zero():
    scores = [time_score(t, 15, 10) for t in range(0, 241)]
    assert all(a > b for a, b in zip(scores, scores[1:]))
    assert scores[-1] > 0


def test_half_life_halves_score_outside_target():
    assert time_score(35, 15, 10) == pytest.approx(time_score(25, 15, 10) / 2)


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        time_score(-1, 15, 10)
    with pytest.raises(ValueError):
        time_score(5, 0, 10)
    with pytest.raises(ValueError):
        time_score(5, 15, 0)


def test_score_shape_comes_from_config():
    cfg = replace(CONFIG, score_max=10, score_at_target=5)
    assert time_score(0, 15, 10, cfg) == 10
    assert time_score(15, 15, 10, cfg) == 5


def test_config_initial_values_match_design_section_8():
    c = CONFIG
    assert (c.monthly_budget_slack_manwon, c.deposit_budget_slack_ratio) == (10, 0.2)
    assert c.score_at_target == 85
    assert (c.department_half_life_min, c.timetable_half_life_min) == (10, 10)
    assert (c.frequent_target_min, c.frequent_half_life_min) == (20, 15)
    assert c.transit_compare_walk_min == 15
    assert (c.transfer_penalty_min, c.wait_penalty_min) == (5, 3)
    assert (c.subway_target_min, c.subway_half_life_min) == (5, 5)
    assert (c.travel_weight_department, c.travel_weight_frequent, c.travel_weight_timetable) == (0.4, 0.4, 0.2)
    assert c.cost_full_score_ratio == 0.2  # 임시 완화 (설계안 초기값 0.6, 분모 0.4 → 0.8)
    assert (c.weight_rank1, c.weight_rank2, c.weight_rank3) == (0.55, 0.30, 0.15)
    assert (c.not_recommended_top_k, c.not_recommended_max) == (3, 3)
    assert c.displayed_top_n == 3


def test_weights_sum_to_one():
    c = CONFIG
    assert c.travel_weight_department + c.travel_weight_frequent + c.travel_weight_timetable == pytest.approx(1)
    assert c.weight_rank1 + c.weight_rank2 + c.weight_rank3 == pytest.approx(1)


def test_department_example_table_uses_only_csv_buildings():
    for buildings in DEPARTMENT_BUILDINGS.values():
        assert buildings
        assert set(buildings) <= CSV_BUILDINGS
