import pytest
from pydantic import ValidationError

from app.data import load_properties
from app.recommend import (
    Profile,
    Weights,
    importance_to_weights,
    lower_is_better,
    monthly_fixed_cost,
    recommend,
    weighted_avg_walk,
)

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}

# 공과대학 주 4회, ECC 주 2회
VISITS = {"공과대학": 4, "ECC": 2}


def ids(items) -> list[str]:
    return [s.property_id for s in items]


# ---------------------------------------------------------------- 가중치

def test_importance_to_weights_equal():
    w = importance_to_weights(3, 3)
    assert w.cost == pytest.approx(0.5)
    assert w.walk == pytest.approx(0.5)


def test_importance_to_weights_ratio():
    w = importance_to_weights(5, 1)
    assert w.cost == pytest.approx(5 / 6)
    assert w.walk == pytest.approx(1 / 6)


def test_weights_normalized_sums_to_one():
    w = Weights(cost=2, walk=6).normalized()
    assert w.cost == pytest.approx(0.25)
    assert w.walk == pytest.approx(0.75)


def test_zero_weights_rejected():
    with pytest.raises(ValueError):
        Weights(cost=0, walk=0).normalized()


def test_profile_importance_must_be_1_to_5():
    with pytest.raises(ValidationError):
        Profile(cost_importance=6)
    with pytest.raises(ValidationError):
        Profile(walk_importance=0)


# ---------------------------------------------------------------- 기준 값 (FE 계산과 같은 값)

def test_monthly_fixed_cost_matches_csv():
    # CSV 의 monthly_housing_cost_manwon 과 같아야 한다 (부동소수점 오차 포함)
    for p in PROPS:
        assert monthly_fixed_cost(p) == pytest.approx(p.monthly_housing_cost_manwon)


def test_monthly_fixed_cost_missing_is_none():
    p = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    assert monthly_fixed_cost(p) is None


def test_weighted_avg_walk_hand_computed():
    # P007: 공과대학 1분, ECC 17분 → (1×4 + 17×2) ÷ 6 = 6.33 → 6.3
    assert weighted_avg_walk(BY_ID["P007"], VISITS) == 6.3
    # P003 은 spot 순서가 다르지만 이름으로 찾는다: (26×4 + 6×2) ÷ 6 = 19.33 → 19.3
    assert weighted_avg_walk(BY_ID["P003"], VISITS) == 19.3


def test_weighted_avg_walk_ignores_zero_visits_and_handles_empty():
    assert weighted_avg_walk(BY_ID["P007"], {"공과대학": 3, "ECC": 0}) == 1.0
    assert weighted_avg_walk(BY_ID["P007"], {}) is None
    assert weighted_avg_walk(BY_ID["P007"], {"ECC": 0}) is None


def test_weighted_avg_walk_unknown_spot_is_none():
    assert weighted_avg_walk(BY_ID["P007"], {"없는건물": 2}) is None


# ---------------------------------------------------------------- 정규화

def test_lower_is_better_min_max():
    out = lower_is_better({"a": 10, "b": 20, "c": 30})
    assert out == {"a": 1.0, "b": 0.5, "c": 0.0}


def test_lower_is_better_all_equal_is_one():
    assert lower_is_better({"a": 5, "b": 5}) == {"a": 1.0, "b": 1.0}


def test_lower_is_better_empty():
    assert lower_is_better({}) == {}


# ---------------------------------------------------------------- 핵심: 가중치가 바뀌면 TOP 3 가 바뀐다

def test_cost_heavy_vs_walk_heavy_top3_differ():
    cost_heavy = recommend(PROPS, Profile(visits=VISITS, cost_importance=5, walk_importance=1))
    walk_heavy = recommend(PROPS, Profile(visits=VISITS, cost_importance=1, walk_importance=5))

    assert ids(cost_heavy.top) == ["P005", "P003", "P006"]
    assert ids(walk_heavy.top) == ["P007", "P002", "P001"]
    assert set(ids(cost_heavy.top)).isdisjoint(ids(walk_heavy.top))


def test_balanced_top3():
    result = recommend(PROPS, Profile(visits=VISITS))
    assert ids(result.top) == ["P007", "P002", "P006"]
    assert [s.rank for s in result.top] == [1, 2, 3]


def test_explicit_weights_override_importance():
    # 프로필 중요도는 비용 중시여도, 가중치를 직접 주면 그 값이 쓰인다.
    profile = Profile(visits=VISITS, cost_importance=5, walk_importance=1)
    result = recommend(PROPS, profile, weights=Weights(cost=1, walk=5))
    assert result.weights.walk == pytest.approx(5 / 6)
    assert ids(result.top) == ["P007", "P002", "P001"]


def test_contributions_add_up_to_score():
    result = recommend(PROPS, Profile(visits=VISITS, cost_importance=2, walk_importance=4))
    for s in result.ranked:
        assert s.contributions.cost + s.contributions.walk - s.penalty == pytest.approx(s.score, abs=1e-3)


def test_ranked_is_sorted_and_ties_break_by_id():
    result = recommend(PROPS, Profile(visits=VISITS))
    scores = [s.score for s in result.ranked]
    assert scores == sorted(scores, reverse=True)

    twin = BY_ID["P002"].model_copy(update={"property_id": "P002-copy"})
    tied = recommend([BY_ID["P002"], twin], Profile(visits=VISITS))
    assert ids(tied.ranked) == ["P002", "P002-copy"]


# ---------------------------------------------------------------- 예산

def test_budget_exclude_drops_over_budget():
    result = recommend(PROPS, Profile(visits=VISITS, budget_manwon=70), budget_mode="exclude")
    assert ids(result.top) == ["P006", "P005", "P003"]
    excluded = {e.property_id: e.reason for e in result.excluded}
    assert excluded["P007"] == "월 예산 초과"
    assert "P006" not in excluded


def test_budget_penalty_keeps_but_demotes():
    plain = recommend(PROPS, Profile(visits=VISITS))
    penalized = recommend(PROPS, Profile(visits=VISITS, budget_manwon=70), budget_mode="penalty")

    # P007 은 예산(70)을 넘지만 이동이 아주 좋아서 감점 뒤에도 2위에 남는다.
    assert ids(penalized.top) == ["P006", "P007", "P005"]
    p007 = next(s for s in penalized.ranked if s.property_id == "P007")
    assert p007.over_budget is True
    assert p007.penalty == 0.2
    before = next(s for s in plain.ranked if s.property_id == "P007").score
    assert p007.score == pytest.approx(before - 0.2, abs=1e-3)
    assert penalized.excluded == []


def test_budget_ignore_only_flags():
    plain = recommend(PROPS, Profile(visits=VISITS))
    ignored = recommend(PROPS, Profile(visits=VISITS, budget_manwon=70), budget_mode="ignore")
    assert ids(ignored.ranked) == ids(plain.ranked)
    assert any(s.over_budget for s in ignored.ranked)


def test_budget_does_not_change_scores_of_survivors():
    # 0~1 맞추는 범위가 예산 탈락 전 전체라서, 예산을 걸어도 남은 매물의 점수는 그대로다.
    plain = {s.property_id: s.score for s in recommend(PROPS, Profile(visits=VISITS)).ranked}
    limited = recommend(PROPS, Profile(visits=VISITS, budget_manwon=70)).ranked
    for s in limited:
        assert s.score == plain[s.property_id]


def test_over_budget_is_none_without_budget():
    result = recommend(PROPS, Profile(visits=VISITS))
    assert all(s.over_budget is None for s in result.ranked)


def test_budget_too_tight_returns_fewer_than_top_n_with_note():
    result = recommend(PROPS, Profile(visits=VISITS, budget_manwon=50))
    assert ids(result.top) == ["P005"]
    assert any("TOP 3" in n for n in result.notes)


# ---------------------------------------------------------------- 값이 없을 때

def test_no_visits_turns_walk_off():
    result = recommend(PROPS, Profile())
    assert result.weights.walk == 0.0
    assert result.weights.cost == pytest.approx(1.0)
    assert ids(result.top) == ["P005", "P003", "P006"]
    assert result.notes  # 이유를 알려준다
    assert all(s.avg_walk_min is None for s in result.ranked)


def test_missing_cost_is_excluded_with_reason():
    broken = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    result = recommend([broken, BY_ID["P002"]], Profile(visits=VISITS))
    assert ids(result.ranked) == ["P002"]
    assert result.excluded[0].property_id == "P001"
    assert "월세 또는 관리비" in result.excluded[0].reason


def test_missing_walk_for_visited_spot_is_excluded():
    broken = BY_ID["P001"].model_copy(update={"spot1_walk_min": None})  # spot1 = ECC
    result = recommend([broken, BY_ID["P002"]], Profile(visits=VISITS))
    assert ids(result.ranked) == ["P002"]
    assert "도보시간" in result.excluded[0].reason


def test_empty_properties_returns_empty_result():
    result = recommend([], Profile(visits=VISITS))
    assert result.top == []
    assert result.ranked == []


def test_top_n_must_be_positive():
    with pytest.raises(ValueError):
        recommend(PROPS, Profile(visits=VISITS), top_n=0)


def test_top_n_limits_top_but_not_ranked():
    result = recommend(PROPS, Profile(visits=VISITS), top_n=5)
    assert len(result.top) == 5
    assert len(result.ranked) == 10
