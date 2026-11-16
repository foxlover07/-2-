"""2장 필터: 대학 → 예산(여유 있음) → (챗봇 제외 조건) → 정보 없음."""

from dataclasses import replace

import pytest

from app.data import load_properties
from app.scoring import (
    RecommendRequest,
    budget_exclusion_reason,
    deposit_exclusion_reason,
    is_over_budget,
    monthly_fixed_cost,
    recommend,
)
from app.scoring_config import CONFIG

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}
# 유지비: P001 87, P002 73.9, P003 55, P004 145, P005 45, P006 67, P007 82, P008 79.9, P009 135, P010 75
# 보증금: P003 2000, P004 300, P005 400, 나머지 1000


def req(**overrides) -> RecommendRequest:
    base = dict(department="컴퓨터공학전공", grade=2, priorities=("travel", "cost"),
                budget_manwon=75, desired_commute_min=15, desired_area_m2=20)
    return RecommendRequest(**{**base, **overrides})


def reasons(result) -> dict[str, str]:
    return {e.property_id: e.reason for e in result.excluded}


# ---------------------------------------------------------------- 유지비

def test_monthly_fixed_cost_matches_csv():
    for p in PROPS:
        assert monthly_fixed_cost(p) == pytest.approx(p.monthly_housing_cost_manwon)


def test_monthly_fixed_cost_missing_is_none():
    assert monthly_fixed_cost(BY_ID["P001"].model_copy(update={"maintenance_manwon": None})) is None


# ---------------------------------------------------------------- 예산 (여유 +10만 원)

def test_budget_slack_zone_passes_and_over_slack_is_excluded():
    assert budget_exclusion_reason(85, 75) is None           # 상한 + 10 까지 통과
    assert budget_exclusion_reason(85.5, 75) is not None
    assert budget_exclusion_reason(87, 75) == "유지비가 상한보다 월 12만 원 많아서 제외"


def test_over_budget_flag():
    assert is_over_budget(82, 75) is True
    assert is_over_budget(75, 75) is False


def test_budget_slack_from_config():
    assert budget_exclusion_reason(80, 75, replace(CONFIG, monthly_budget_slack_manwon=0)) is not None


# ---------------------------------------------------------------- 보증금 (× 1.2)

def test_deposit_filter_only_when_given():
    assert deposit_exclusion_reason(2000, None) is None
    assert deposit_exclusion_reason(2000, 1000) == "보증금이 상한보다 1000만 원 많아서 제외"   # 1200 < 2000
    assert deposit_exclusion_reason(1200, 1000) is None                                      # 정확히 × 1.2
    assert deposit_exclusion_reason(None, 1000) == "보증금 정보 없음"


# ---------------------------------------------------------------- 순서와 사유 (recommend)

def test_filters_in_order_with_reasons_and_stage():
    r = recommend(PROPS, req(deposit_budget_manwon=1000))
    by = {e.property_id: e for e in r.excluded}
    assert by["P001"].reason == "유지비가 상한보다 월 12만 원 많아서 제외" and by["P001"].stage == "budget"
    assert by["P004"].stage == "budget" and by["P009"].stage == "budget"
    assert by["P003"].reason == "보증금이 상한보다 1000만 원 많아서 제외" and by["P003"].stage == "deposit"
    ranked = {x.property_id for x in r.ranked}
    assert ranked == {"P002", "P005", "P006", "P007", "P008", "P010"}


def test_slack_zone_listings_stay_and_are_marked_over_budget():
    r = recommend(PROPS, req(budget_manwon=75))
    flags = {x.property_id: x.over_budget for x in r.ranked}
    assert flags["P007"] is True and flags["P008"] is True     # 82, 79.9: 상한 75 ~ 85 사이
    assert flags["P010"] is False                              # 정확히 75
    assert flags["P005"] is False


def test_missing_info_is_excluded_last():
    no_maint = BY_ID["P002"].model_copy(update={"maintenance_manwon": None})
    no_walk = BY_ID["P006"].model_copy(update={"spot4_walk_min": None})  # spot4 = 공과대학
    no_area = BY_ID["P005"].model_copy(update={"area_m2": None})
    r = recommend([no_maint, no_walk, no_area, BY_ID["P007"]], req())
    rs = reasons(r)
    assert rs["P002"] == "월세 또는 관리비 정보 없음"
    assert rs["P006"] == "도보시간 정보 없음: 공과대학"
    assert rs["P005"] == "면적 정보 없음"
    assert all(e.stage == "missing" for e in r.excluded)
    assert [x.property_id for x in r.ranked] == ["P007"]


def test_budget_checked_before_missing_walk():
    # 예산으로 빠지는 매물은 도보시간이 없어도 예산 사유로 빠진다 (필터 순서)
    p = BY_ID["P004"].model_copy(update={"spot4_walk_min": None})
    assert reasons(recommend([p, BY_ID["P007"]], req()))["P004"].startswith("유지비가 상한보다")


def test_duplicate_ids_are_excluded_with_reason():
    r = recommend([BY_ID["P002"], BY_ID["P002"]], req())
    assert [x.property_id for x in r.ranked] == ["P002"]
    assert r.excluded[0].reason == "매물 ID 중복" and r.excluded[0].stage == "duplicate"


# ---------------------------------------------------------------- 대학 (11/10, v3 테스트에서 옮김)

def test_only_requested_university_and_others_not_listed():
    ku = [p.model_copy(update={"property_id": "ku:" + p.property_id, "university": "고려대학교", "spot1_name": "정경관"})
          for p in PROPS]
    r_ewha = recommend(PROPS + ku, req())
    assert all(not x.property_id.startswith("ku:") for x in r_ewha.ranked + r_ewha.excluded)
    r_ku = recommend(PROPS + ku, req(university="고려대학교", department_buildings=["정경관"], budget_manwon=200))
    assert r_ku.ranked and all(x.property_id.startswith("ku:") for x in r_ku.ranked + r_ku.excluded)
    assert r_ku.assumptions["university"] == "고려대학교"


def test_unknown_university_has_no_listings():
    r = recommend(PROPS, req(university="없는대학교", department_buildings=["ECC"]))
    assert r.ranked == [] and r.excluded == []
