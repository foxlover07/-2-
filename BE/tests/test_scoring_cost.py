"""2단계: 예산 필터 + 주거비 점수 (설계안 2장, 4.3장)."""

from dataclasses import replace

import pytest

from app.data import load_properties
from app.scoring import budget_exclusion_reason, cost_score, is_over_budget, monthly_fixed_cost
from app.scoring_config import CONFIG

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}
# 월 고정 주거비: P001 87, P002 73.9, P003 55, P004 145, P005 45, P006 67, P007 82, P008 79.9, P009 135, P010 75
# 보증금: P003 2000, P004 300, P005 400, 나머지 1000


def with_cost(p, rent, maintenance):
    return p.model_copy(update={"monthly_rent_manwon": rent, "maintenance_manwon": maintenance})


# ---------------------------------------------------------------- 월 고정 주거비 (기존 test_recommend.py 에서 옮김)

def test_monthly_fixed_cost_matches_csv():
    # CSV 의 monthly_housing_cost_manwon 과 같아야 한다 (부동소수점 오차 포함)
    for p in PROPS:
        assert monthly_fixed_cost(p) == pytest.approx(p.monthly_housing_cost_manwon)


def test_monthly_fixed_cost_missing_is_none():
    p = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    assert monthly_fixed_cost(p) is None


# ---------------------------------------------------------------- 예산 필터

def test_within_budget_passes():
    assert budget_exclusion_reason(BY_ID["P003"], budget_manwon=60) is None  # 55


def test_slack_zone_passes_but_is_over_budget():
    # 예산 80 + 여유 10 = 90 까지 통과. P001 87 은 통과하지만 예산 초과 표시
    assert budget_exclusion_reason(BY_ID["P001"], budget_manwon=80) is None
    assert is_over_budget(87, 80) is True
    assert is_over_budget(80, 80) is False


def test_exactly_budget_plus_slack_passes_and_just_over_is_excluded():
    p = with_cost(BY_ID["P001"], 80, 10)  # 90
    assert budget_exclusion_reason(p, budget_manwon=80) is None
    p = with_cost(BY_ID["P001"], 80, 10.5)  # 90.5
    assert budget_exclusion_reason(p, budget_manwon=80) is not None


def test_monthly_over_slack_is_excluded_with_amount():
    # 설계안 예시 문구: "예산보다 월 12만 원 비싸서 제외"
    p = with_cost(BY_ID["P001"], 70, 12)  # 82, 예산 70 → 12만 원 초과 (> 여유 10)
    assert budget_exclusion_reason(p, budget_manwon=70) == "예산보다 월 12만 원 비싸서 제외"


def test_monthly_cost_missing_is_excluded():
    p = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    assert budget_exclusion_reason(p, budget_manwon=100) == "월세 또는 관리비 정보 없음"


def test_deposit_filter_only_when_deposit_budget_given():
    p003 = BY_ID["P003"]  # 보증금 2000, 월 55
    assert budget_exclusion_reason(p003, budget_manwon=60) is None
    # 1000 × 1.2 = 1200 < 2000 → 제외
    assert budget_exclusion_reason(p003, 60, deposit_budget_manwon=1000) == "보증금이 예산보다 1000만 원 많아서 제외"
    # 1700 × 1.2 = 2040 ≥ 2000 → 통과
    assert budget_exclusion_reason(p003, 60, deposit_budget_manwon=1700) is None


def test_deposit_exactly_slack_passes():
    p = BY_ID["P001"]  # 보증금 1000
    assert budget_exclusion_reason(p, 100, deposit_budget_manwon=1000 / 1.2) is None


def test_deposit_missing_is_excluded_only_when_deposit_budget_given():
    p = BY_ID["P001"].model_copy(update={"deposit_manwon": None})
    assert budget_exclusion_reason(p, 100) is None
    assert budget_exclusion_reason(p, 100, deposit_budget_manwon=1000) == "보증금 정보 없음"


def test_slack_comes_from_config():
    cfg = replace(CONFIG, monthly_budget_slack_manwon=0)
    assert budget_exclusion_reason(BY_ID["P001"], budget_manwon=80, cfg=cfg) is not None  # 87 > 80 + 0


# ---------------------------------------------------------------- 주거비 점수

@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (10, 100),   # 예산의 20% 이하 → 100점
        (20, 100),   # 정확히 20%
        (60, 50),    # 100 × (100 − 60) ÷ 80
        (70, 37.5),
        (90, 12.5),
        (100, 0),    # 예산과 같으면 0점
        (105, 0),    # 여유 구간도 0점
    ],
)
def test_cost_score_budget_100(cost, expected):
    assert cost_score(cost, 100) == pytest.approx(expected)


def test_cost_score_is_linear():
    # 1만 원 차이가 어느 구간에서나 같은 점수 차이
    assert cost_score(70, 100) - cost_score(71, 100) == pytest.approx(cost_score(90, 100) - cost_score(91, 100))


def test_cost_score_is_absolute_not_relative():
    # 다른 매물과 상관없이 예산과 비용만으로 정해진다
    assert cost_score(55, 80) == pytest.approx(100 * (80 - 55) / 64)


def test_cost_score_rejects_zero_budget():
    with pytest.raises(ValueError):
        cost_score(50, 0)


def test_cost_full_score_ratio_from_config():
    cfg = replace(CONFIG, cost_full_score_ratio=0.5)
    assert cost_score(50, 100, cfg) == 100
    assert cost_score(75, 100, cfg) == pytest.approx(50)
