"""2단계: 예산 필터 + 주거비 점수 (설계안 2장, 4.3장 · 2026-11-08 결정: 예산은 필터만, 주거비는 상대 비교)."""

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


def test_exactly_budget_passes_and_just_over_is_excluded():
    # 예산은 1차 필터로만 쓰고 여유 구간이 없다 (2026-11-08 결정)
    p = with_cost(BY_ID["P001"], 70, 10)  # 80
    assert budget_exclusion_reason(p, budget_manwon=80) is None
    p = with_cost(BY_ID["P001"], 70, 10.5)  # 80.5
    assert budget_exclusion_reason(p, budget_manwon=80) == "예산보다 월 0.5만 원 비싸서 제외"


def test_no_over_budget_zone():
    # 예산을 넘는 매물은 필터에서 빠지므로 추천 결과에 '예산 초과' 매물이 없다
    assert budget_exclusion_reason(BY_ID["P001"], budget_manwon=80) == "예산보다 월 7만 원 비싸서 제외"  # 87
    assert is_over_budget(80, 80) is False


def test_monthly_over_budget_is_excluded_with_amount():
    # 설계안 예시 문구: "예산보다 월 12만 원 비싸서 제외"
    p = with_cost(BY_ID["P001"], 70, 12)  # 82, 예산 70
    assert budget_exclusion_reason(p, budget_manwon=70) == "예산보다 월 12만 원 비싸서 제외"


def test_monthly_cost_missing_is_excluded():
    p = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    assert budget_exclusion_reason(p, budget_manwon=100) == "월세 또는 관리비 정보 없음"


def test_deposit_filter_only_when_deposit_budget_given():
    p003 = BY_ID["P003"]  # 보증금 2000, 월 55
    assert budget_exclusion_reason(p003, budget_manwon=60) is None
    assert budget_exclusion_reason(p003, 60, deposit_budget_manwon=1999) == "보증금이 예산보다 1만 원 많아서 제외"
    assert budget_exclusion_reason(p003, 60, deposit_budget_manwon=2000) is None  # 같으면 통과


def test_deposit_missing_is_excluded_only_when_deposit_budget_given():
    p = BY_ID["P001"].model_copy(update={"deposit_manwon": None})
    assert budget_exclusion_reason(p, 100) is None
    assert budget_exclusion_reason(p, 100, deposit_budget_manwon=1000) == "보증금 정보 없음"


def test_slack_comes_from_config():
    # 여유는 설정값으로 다시 켤 수 있다 (설계안 초기값 월 +10만 원)
    cfg = replace(CONFIG, monthly_budget_slack_manwon=10)
    assert budget_exclusion_reason(BY_ID["P001"], budget_manwon=80, cfg=cfg) is None  # 87 ≤ 80 + 10


# ---------------------------------------------------------------- 주거비 점수 (필터 통과 매물 안 상대 비교)

@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (40, 100),   # 가장 싼 매물
        (50, 75),    # 100 × (80 − 50) ÷ (80 − 40)
        (60, 50),
        (80, 0),     # 가장 비싼 매물
    ],
)
def test_cost_score_relative_between_lowest_and_highest(cost, expected):
    assert cost_score(cost, 40, 80) == pytest.approx(expected)


def test_cost_score_is_linear():
    assert cost_score(50, 40, 80) - cost_score(51, 40, 80) == pytest.approx(cost_score(70, 40, 80) - cost_score(71, 40, 80))


def test_cost_score_does_not_use_budget():
    # 같은 매물 묶음이면 예산과 상관없이 같은 점수 (예산은 필터에만)
    assert cost_score(60, 40, 80) == cost_score(60, 40, 80)
    assert "budget" not in cost_score.__code__.co_varnames


def test_cost_score_all_same_is_full():
    assert cost_score(55, 55, 55) == 100


def test_cost_score_rejects_inverted_range():
    with pytest.raises(ValueError):
        cost_score(50, 80, 40)
