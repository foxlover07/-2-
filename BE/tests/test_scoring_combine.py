"""4장 종합 점수 (1·2·3순위 → 0.55/0.30/0.15) + 결과 (비추천 사유, assumptions, notes)."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.data import load_properties
from app.scoring import (
    Candidate,
    RecommendRequest,
    UnknownBuildingError,
    applied_weights,
    full_priorities,
    not_recommended,
    rank_weights,
    recommend,
    travel_score,
)
from app.scoring_config import CONFIG

PROPS = list(load_properties())
BY_ID = {p.property_id: p for p in PROPS}


def req(**overrides) -> RecommendRequest:
    base = dict(
        department="컴퓨터공학전공",  # 예시표: 공과대학
        grade=2,
        priorities=("travel", "cost"),
        budget_manwon=75,
        desired_commute_min=15,
        desired_area_m2=20,
    )
    return RecommendRequest(**{**base, **overrides})


def ids(items) -> list[str]:
    return [x.property_id for x in items]


# ---------------------------------------------------------------- 1·2순위 → 가중치

def test_full_priorities_adds_third():
    assert full_priorities(("cost", "travel")) == ("cost", "travel", "area")
    assert full_priorities(("area", "cost")) == ("area", "cost", "travel")


def test_rank_weights_055_030_015():
    assert rank_weights(("travel", "cost")) == {"travel": 0.55, "cost": 0.30, "area": 0.15}
    assert rank_weights(("area", "travel")) == {"area": 0.55, "travel": 0.30, "cost": 0.15}


def test_applied_weights_redistributes_unavailable_factor():
    w = applied_weights(rank_weights(("travel", "cost")), ["travel", "cost"])
    assert w == pytest.approx({"travel": 0.55 / 0.85, "cost": 0.30 / 0.85, "area": 0.0})


def test_all_three_factors_used_now():
    r = recommend(PROPS, req())
    assert r.weights == pytest.approx({"travel": 0.55, "cost": 0.30, "area": 0.15})
    assert r.priorities == ("travel", "cost", "area")


# ---------------------------------------------------------------- 입력 검증

def test_priorities_must_differ_and_be_known():
    with pytest.raises(ValidationError):
        req(priorities=("cost", "cost"))
    with pytest.raises(ValidationError):
        req(priorities=("travel", "transit"))   # v2 의 교통 편의성은 삭제


def test_required_inputs():
    with pytest.raises(ValidationError):
        req(budget_manwon=0)
    with pytest.raises(ValidationError):
        req(desired_commute_min=0)
    with pytest.raises(ValidationError):
        req(desired_area_m2=0)
    with pytest.raises(ValidationError):
        req(larger_area="maybe")


def test_department_table_is_ewha_only():
    assert req().resolved_department_buildings() == ["공과대학"]
    with pytest.raises(ValidationError):
        req(department="없는학과")
    with pytest.raises(ValidationError):
        req(university="고려대학교")              # 예시표는 이대만 → 다른 대학은 건물을 직접
    assert req(university="고려대학교", department_buildings=["정경관"]).resolved_department_buildings() == ["정경관"]


def test_unknown_building_raises():
    with pytest.raises(UnknownBuildingError):
        recommend(PROPS, req(department_buildings=["중앙도서관"]))


# ---------------------------------------------------------------- 종합 점수 · 순위

def test_score_is_weighted_sum_and_contributions_add_up():
    r = recommend(PROPS, req())
    for s in r.ranked:
        expected = sum(r.weights[f] * s.factor_scores[f] for f in ("travel", "cost", "area"))
        assert s.score == pytest.approx(expected, abs=0.02)
        assert sum(s.contributions.values()) == pytest.approx(s.score, abs=0.02)


def test_hand_computed_p007():
    # P007: 공과대학 1분, 유지비 82(상한 75), 면적 25.1㎡ (희망 20, 상관없음)
    r = recommend(PROPS, req())
    p = next(x for x in r.ranked if x.property_id == "P007")
    assert p.factor_scores == {"travel": 57.0, "cost": 36.0, "area": 50.0}
    assert p.vs_target.model_dump() == {"travel_min": -14.0, "cost_manwon": 7.0, "area_m2": 5.1}
    assert p.score == pytest.approx(0.55 * 57 + 0.30 * 36 + 0.15 * 50)


def test_larger_area_choice_changes_area_score():
    like = recommend(PROPS, req(larger_area="like"))
    dislike = recommend(PROPS, req(larger_area="dislike"))
    p7 = lambda r: next(x for x in r.ranked if x.property_id == "P007").factor_scores["area"]  # noqa: E731
    assert p7(like) == pytest.approx(62.75, abs=0.01)      # 5.1㎡ 넓음 → +5 × 2.55 (응답은 소수 둘째 자리)
    assert p7(dislike) == pytest.approx(43.625, abs=0.01)  # −2.5 × 2.55


def test_ranked_sorted_by_score_then_cost_then_id():
    r = recommend(PROPS, req())
    keys = [(-x.score, x.monthly_cost_manwon, x.property_id) for x in r.ranked]
    assert keys == sorted(keys)
    assert [x.rank for x in r.ranked] == list(range(1, len(r.ranked) + 1))


def test_ties_break_by_cost_then_id():
    twin = BY_ID["P002"].model_copy(update={"property_id": "P002-copy"})
    r = recommend([twin, BY_ID["P002"]], req(budget_manwon=100))
    assert ids(r.ranked) == ["P002", "P002-copy"]
    # 예산이 넉넉하면 주거비 점수가 둘 다 100점으로 같다 → 종합 점수 동점 → 유지비 낮은 쪽이 ID 와 상관없이 먼저
    cheaper = BY_ID["P002"].model_copy(update={"property_id": "P999", "monthly_rent_manwon": 56})  # 64.9
    r = recommend([BY_ID["P002"], cheaper], req(budget_manwon=200))
    assert r.ranked[0].score == r.ranked[1].score
    assert ids(r.ranked) == ["P999", "P002"]


def test_priorities_change_order_but_not_factor_scores():
    travel_first = recommend(PROPS, req(priorities=("travel", "cost")))
    cost_first = recommend(PROPS, req(priorities=("cost", "travel")))
    assert ids(travel_first.ranked) != ids(cost_first.ranked)
    a = {s.property_id: s.factor_scores for s in travel_first.ranked}
    b = {s.property_id: s.factor_scores for s in cost_first.ranked}
    assert a == b


# ---------------------------------------------------------------- notes · assumptions

def test_notes_for_missing_parts():
    r = recommend(PROPS, req())
    assert any("자주 가는 곳은 데이터 준비 중" in n for n in r.notes)
    assert any("시간표를 입력하지 않아" in n for n in r.notes)
    with_tt = recommend(PROPS, req(timetable=[{"day": "월", "start": "09:00", "end": "10:00", "building": "ECC"}]))
    assert not any("시간표를 입력하지 않아" in n for n in with_tt.notes)


def test_too_few_candidates_note():
    r = recommend(PROPS, req(budget_manwon=35))   # 45 ≤ 35 + 10 인 P005 만
    assert ids(r.ranked) == ["P005"]
    assert any("1개뿐" in n for n in r.notes)


def test_assumptions():
    a = recommend(PROPS, req(deposit_budget_manwon=1000, larger_area="like")).assumptions
    assert a["monthly_limit_manwon"] == 85
    assert a["deposit_limit_manwon"] == 1200
    assert a["desired_commute_min"] == 15 and a["desired_area_m2"] == 20
    assert a["larger_area"] == "like" and a["larger_area_slope_per_unit"] == 5
    assert a["weights_applied"] == pytest.approx({"travel": 0.55, "cost": 0.30, "area": 0.15})
    assert a["unavailable_travel_parts"] == ["frequent", "timetable"]
    assert a["score_rule"]["units"] == {"travel_min": 10, "cost_manwon": 5, "area_m2": 2}
    assert a["exclusions"]["plans"] == []
    assert a["config"]["base_score"] == 50


# ---------------------------------------------------------------- 비추천 사유

def cand(pid, *, travel=None, cost=50.0, area=50.0, monthly_cost=60.0, area_m2=20.0, over_budget=False, travel_value=None):
    return Candidate(
        property_id=pid,
        factor_scores={"travel": travel.score if travel else travel_value, "cost": cost, "area": area},
        travel=travel,
        monthly_cost=monthly_cost,
        deposit=1000,
        area=area_m2,
        over_budget=over_budget,
    )


SCREEN_1 = replace(CONFIG, displayed_top_n=1)
NR = dict(budget=75, desired_area=20)


def test_weak_travel_message_uses_lowest_scoring_part():
    near = cand("A", travel=travel_score(15, department_avg_min=2, timetable_avg_min=3), cost=40, monthly_cost=77)
    # B: 학과 건물 5분(55점)은 좋지만 시간표 65분(0점)이 가장 낮은 부분. 이동 시간 1순위에서 A(약 52.0) > B(약 50.2)
    cheap = cand("B", travel=travel_score(15, department_avg_min=5, timetable_avg_min=65), cost=100, monthly_cost=25)
    result = not_recommended([near, cheap], ("travel", "area"), ["travel", "cost", "area"], cfg=SCREEN_1, **NR)
    assert ids(result) == ["B"]
    nr = result[0]
    assert nr.weak.factor == "travel" and nr.weak.part == "timetable" and nr.weak.value == 65
    assert nr.strong.factor == "cost"
    assert nr.message == "1위 매물보다 싸지만 시간표 기준 편도 평균 65분이라 희망 통학 시간(15분)보다 50분 길어요."


def test_weak_cost_message_over_budget():
    cheap = cand("A", travel=travel_score(15, department_avg_min=20), cost=100, monthly_cost=25)
    near = cand("B", travel=travel_score(15, department_avg_min=1), cost=36, monthly_cost=82, over_budget=True)
    nr = not_recommended([cheap, near], ("cost", "area"), ["travel", "cost", "area"], cfg=SCREEN_1, **NR)[0]
    assert nr.property_id == "B" and nr.weak.factor == "cost"
    assert nr.message == "1위 매물보다 가깝지만 유지비가 월 82만 원으로 상한(75만 원)보다 7만 원 많아요."


def test_weak_area_message_smaller_than_desired():
    roomy = cand("A", area=75, area_m2=30)
    tiny = cand("B", area=30, area_m2=16, cost=100, monthly_cost=25)
    nr = not_recommended([roomy, tiny], ("area", "travel"), ["cost", "area"], cfg=SCREEN_1, **NR)[0]
    assert nr.property_id == "B" and nr.weak.factor == "area"
    assert nr.message == "1위 매물보다 싸지만 면적이 16㎡로 희망 면적(20㎡)보다 4㎡ 좁아요."


def test_null_factor_is_never_weak():
    top = cand("A", travel_value=None, cost=60, area=90)
    b = cand("B", travel_value=None, cost=100, area=40, monthly_cost=25)
    result = not_recommended([top, b], ("area", "cost"), ["cost", "area"], cfg=SCREEN_1, **NR)
    assert ids(result) == ["B"] and result[0].weak.factor == "area"


def test_not_recommended_respects_displayed_top_n_and_top_k():
    for n in (1, 3, 5):
        r = recommend(PROPS, req(budget_manwon=100), cfg=replace(CONFIG, displayed_top_n=n))
        assert not set(ids(r.ranked[:n])) & set(ids(r.not_recommended))
        for nr in r.not_recommended:
            assert nr.best_rank_elsewhere <= CONFIG.not_recommended_top_k
            assert all(tuple(h.priorities) != ("travel", "cost") for h in nr.top_in)


def test_not_recommended_max_and_order_by_best_rank_then_id():
    cands = [
        cand("C1", travel_value=100, cost=0, area=0),
        cand("C2", travel_value=0, cost=100, area=0),
        cand("C3", travel_value=0, cost=0, area=100),
        cand("C4", travel_value=90, cost=10, area=0),
        cand("C5", travel_value=10, cost=90, area=0),
        cand("C6", travel_value=0, cost=10, area=90),
        cand("C7", travel_value=50, cost=50, area=50),
    ]
    args = (cands, ("travel", "cost"), ["travel", "cost", "area"])
    everyone = not_recommended(*args, cfg=replace(CONFIG, displayed_top_n=1, not_recommended_max=99), **NR)
    assert len(everyone) > 3
    expected = sorted(everyone, key=lambda r: (r.best_rank_elsewhere, r.property_id))[:3]
    picked = not_recommended(*args, cfg=replace(CONFIG, displayed_top_n=1), **NR)
    assert ids(picked) == ids(expected)


def test_real_data_not_recommended_has_message():
    r = recommend(PROPS, req(budget_manwon=100))
    assert r.not_recommended
    assert all(nr.message for nr in r.not_recommended)
