"""4단계: 종합 점수 + 1·2순위 가중치 + 비추천 사유 + assumptions (설계안 5장, 6장)."""

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
        budget_manwon=75,
        department="컴퓨터공학전공",  # 예시표: 공과대학
        grade=2,
        desired_commute_min=15,
        priorities=("travel", "cost"),
    )
    return RecommendRequest(**{**base, **overrides})


def ids(items) -> list[str]:
    return [x.property_id for x in items]


# ---------------------------------------------------------------- 1·2순위 → 가중치

def test_full_priorities_adds_third():
    assert full_priorities(("cost", "travel")) == ("cost", "travel", "transit")
    assert full_priorities(("transit", "cost")) == ("transit", "cost", "travel")


def test_rank_weights_055_030_015():
    assert rank_weights(("travel", "cost")) == {"travel": 0.55, "cost": 0.30, "transit": 0.15}
    assert rank_weights(("transit", "travel")) == {"transit": 0.55, "travel": 0.30, "cost": 0.15}


def test_applied_weights_redistributes_unavailable_factor():
    w = applied_weights(rank_weights(("travel", "cost")), ["travel", "cost"])
    assert w == pytest.approx({"travel": 0.55 / 0.85, "cost": 0.30 / 0.85, "transit": 0.0})
    assert sum(w.values()) == pytest.approx(1)


def test_applied_weights_when_transit_is_first():
    # 교통 편의성 1순위 → 남은 주거비 0.30 : 이동시간 0.15 = 2 : 1
    w = applied_weights(rank_weights(("transit", "cost")), ["travel", "cost"])
    assert w == pytest.approx({"cost": 2 / 3, "travel": 1 / 3, "transit": 0.0})


def test_rank_weights_from_config():
    cfg = replace(CONFIG, weight_rank1=0.6, weight_rank2=0.3, weight_rank3=0.1)
    assert rank_weights(("cost", "travel"), cfg) == {"cost": 0.6, "travel": 0.3, "transit": 0.1}


# ---------------------------------------------------------------- 입력 검증

def test_priorities_must_differ():
    with pytest.raises(ValidationError):
        req(priorities=("cost", "cost"))


def test_priorities_must_be_known_factors():
    with pytest.raises(ValidationError):
        req(priorities=("travel", "safety"))


def test_unknown_department_needs_buildings():
    with pytest.raises(ValidationError):
        req(department="없는학과")
    r = req(department="없는학과", department_buildings=["ECC"])
    assert r.resolved_department_buildings() == ["ECC"]


def test_department_buildings_override_table():
    assert req().resolved_department_buildings() == ["공과대학"]
    assert req(department_buildings=["학관"]).resolved_department_buildings() == ["학관"]


def test_budget_and_commute_must_be_positive():
    with pytest.raises(ValidationError):
        req(budget_manwon=0)
    with pytest.raises(ValidationError):
        req(desired_commute_min=0)


def test_unknown_building_raises():
    with pytest.raises(UnknownBuildingError):
        recommend(PROPS, req(department_buildings=["중앙도서관"]))
    with pytest.raises(UnknownBuildingError):
        recommend(PROPS, req(timetable=[{"day": "월", "start": "09:00", "end": "10:00", "building": "포관"}]))


# ---------------------------------------------------------------- 종합 점수 · 순위

def test_score_is_weighted_sum_and_contributions_add_up():
    result = recommend(PROPS, req())
    w = result.weights
    for s in result.ranked:
        expected = w["travel"] * s.factor_scores["travel"] + w["cost"] * s.factor_scores["cost"]
        assert s.score == pytest.approx(expected, abs=0.02)
        assert sum(s.contributions.values()) == pytest.approx(s.score, abs=0.02)
        assert s.factor_scores["transit"] is None


def test_ranked_sorted_and_rank_numbers():
    result = recommend(PROPS, req())
    scores = [s.score for s in result.ranked]
    assert scores == sorted(scores, reverse=True)
    assert [s.rank for s in result.ranked] == list(range(1, len(result.ranked) + 1))


def test_ties_break_by_id():
    # (기존 test_recommend.py 에서 옮김)
    twin = BY_ID["P002"].model_copy(update={"property_id": "P002-copy"})
    result = recommend([twin, BY_ID["P002"]], req(budget_manwon=100))
    assert ids(result.ranked) == ["P002", "P002-copy"]


def test_priorities_change_order():
    travel_first = recommend(PROPS, req(priorities=("travel", "cost")))
    cost_first = recommend(PROPS, req(priorities=("cost", "travel")))
    assert ids(travel_first.ranked) != ids(cost_first.ranked)
    # 요소별 점수는 순위 조합과 상관없이 같다 (절대 점수)
    a = {s.property_id: s.factor_scores for s in travel_first.ranked}
    b = {s.property_id: s.factor_scores for s in cost_first.ranked}
    assert a == b


def test_same_school_different_department_gets_different_top1():
    eng = recommend(PROPS, req(budget_manwon=100, priorities=("travel", "cost")))
    ecc = recommend(PROPS, req(budget_manwon=100, department_buildings=["ECC"], priorities=("travel", "cost")))
    assert eng.ranked[0].property_id != ecc.ranked[0].property_id


def test_over_budget_marked_and_cost_score_zero():
    result = recommend(PROPS, req(budget_manwon=75))
    p007 = next(s for s in result.ranked if s.property_id == "P007")  # 82 (75 ~ 85 여유 구간)
    assert p007.over_budget is True
    assert p007.factor_scores["cost"] == 0
    p005 = next(s for s in result.ranked if s.property_id == "P005")  # 45 ≤ 75 × 0.6
    assert p005.over_budget is False
    assert p005.factor_scores["cost"] == 100


def test_result_limit_from_config():
    result = recommend(PROPS, req(budget_manwon=200), cfg=replace(CONFIG, result_limit=4))
    assert len(result.ranked) == 4


# ---------------------------------------------------------------- 제외 (기존 경계 상황 테스트에서 옮김)

def test_excluded_with_reasons():
    result = recommend(PROPS, req(budget_manwon=75))
    reasons = {e.property_id: e.reason for e in result.excluded}
    assert reasons["P001"] == "예산보다 월 12만 원 비싸서 제외"  # 87
    assert "P001" not in ids(result.ranked)


def test_missing_cost_is_excluded_with_reason():
    broken = BY_ID["P001"].model_copy(update={"maintenance_manwon": None})
    result = recommend([broken, BY_ID["P002"]], req(budget_manwon=100))
    assert ids(result.ranked) == ["P002"]
    assert result.excluded[0].reason == "월세 또는 관리비 정보 없음"


def test_missing_walk_for_needed_building_is_excluded():
    broken = BY_ID["P001"].model_copy(update={"spot4_walk_min": None})  # spot4 = 공과대학
    result = recommend([broken, BY_ID["P002"]], req(budget_manwon=100))
    assert ids(result.ranked) == ["P002"]
    assert result.excluded[0].reason == "도보시간 정보 없음: 공과대학"


def test_duplicate_ids_are_excluded_with_reason():
    result = recommend([BY_ID["P002"], BY_ID["P002"]], req(budget_manwon=100))
    assert ids(result.ranked) == ["P002"]
    assert result.excluded[0].reason == "매물 ID 중복"


def test_empty_properties_returns_empty_result():
    result = recommend([], req())
    assert result.ranked == []
    assert result.not_recommended == []


def test_too_few_candidates_note():
    result = recommend(PROPS, req(budget_manwon=40))  # 45 ≤ 50 인 P005 만
    assert ids(result.ranked) == ["P005"]
    assert any("1개뿐" in n for n in result.notes)


# ---------------------------------------------------------------- 안내 (notes)

def test_transit_chosen_as_priority_note():
    # 요청 1: 교통 편의성을 1·2순위로 골랐는데 데이터가 없으면 순위가 반영되지 않았다고 알린다
    for priorities in (("transit", "cost"), ("cost", "transit")):
        result = recommend(PROPS, req(priorities=priorities))
        assert any("교통 편의성은 아직 계산할 수 없어 선택한 순위가 반영되지 않았습니다" in n for n in result.notes)
        assert result.weights["transit"] == 0
        assert result.weights["travel"] + result.weights["cost"] == pytest.approx(1)


def test_transit_third_has_no_priority_note_but_redistribution_note():
    result = recommend(PROPS, req(priorities=("travel", "cost")))
    assert not any("선택한 순위가 반영되지 않았습니다" in n for n in result.notes)
    assert any("교통 편의성은 아직 계산할 수 없어 비중을" in n for n in result.notes)


def test_missing_timetable_and_frequent_notes():
    result = recommend(PROPS, req())
    assert any("자주 가는 곳" in n for n in result.notes)
    assert any("시간표를 입력하지 않아" in n for n in result.notes)
    with_tt = recommend(PROPS, req(timetable=[{"day": "월", "start": "09:00", "end": "10:00", "building": "ECC"}]))
    assert not any("시간표를 입력하지 않아" in n for n in with_tt.notes)


# ---------------------------------------------------------------- assumptions

def test_assumptions_include_targets_weights_and_slack():
    a = recommend(PROPS, req(deposit_budget_manwon=1000)).assumptions
    assert a["time_score"]["department"] == {"T": 15, "h": 10, "mode": "도보"}
    assert a["weights_by_rank"] == {"travel": 0.55, "cost": 0.30, "transit": 0.15}
    assert a["weights_applied"]["transit"] == 0
    assert a["unavailable_factors"] == ["transit"]
    assert a["unavailable_travel_parts"] == ["frequent", "timetable"]
    assert a["monthly_limit_manwon"] == 85
    assert a["deposit_limit_manwon"] == 1200
    assert a["grade"] == 2
    assert a["config"]["monthly_budget_slack_manwon"] == 10
    assert a["config"]["displayed_top_n"] == 3


# ---------------------------------------------------------------- 비추천 사유

def cand(pid, *, travel=None, cost=None, transit=None, monthly_cost=60.0, over_budget=False, travel_value=None):
    t = travel.score if travel else travel_value
    return Candidate(
        property_id=pid,
        factor_scores={"travel": t, "transit": transit, "cost": cost},
        travel=travel,
        monthly_cost=monthly_cost,
        deposit=1000,
        over_budget=over_budget,
    )


SCREEN_1 = replace(CONFIG, displayed_top_n=1)


def test_weak_department_message_matches_design_example():
    # 설계안 6장 예시: "가장 저렴하지만 학과 건물까지 편도 26분이라 희망 통학 시간(15분)보다 11분 길어요."
    # 이동시간 1순위: A 0.647 × 98 ≈ 63.4  >  B 0.647 × 39.7 + 0.353 × 100 ≈ 61.0 → A 1위, B 는 주거비 1순위 조합에서 1위
    near = cand("A", travel=travel_score(15, department_avg_min=2), cost=0, monthly_cost=80)
    cheap = cand("B", travel=travel_score(15, department_avg_min=26), cost=100, monthly_cost=45)
    result = not_recommended([near, cheap], ("travel", "cost"), ["travel", "cost"], budget=80, cfg=SCREEN_1)
    assert [r.property_id for r in result] == ["B"]
    nr = result[0]
    assert nr.weak.factor == "travel"
    assert nr.weak.part == "department"
    assert nr.strong.factor == "cost"
    assert nr.message == "1위 매물보다 저렴하지만 학과 건물까지 편도 26분이라 희망 통학 시간(15분)보다 11분 길어요."


def test_weak_travel_uses_lowest_scoring_part():
    # 요청 3: 이동시간이 약한 요소면 세부 부분 중 점수가 가장 낮은 부분(여기서는 시간표)의 값을 쓴다
    # B: 학과 건물 5분(95점)은 좋지만 시간표 40분(약 15점)이 가장 낮은 부분
    near = cand("A", travel=travel_score(15, department_avg_min=2, timetable_avg_min=3), cost=50, monthly_cost=60)
    cheap = cand("B", travel=travel_score(15, department_avg_min=5, timetable_avg_min=40), cost=100, monthly_cost=45)
    result = not_recommended([near, cheap], ("travel", "cost"), ["travel", "cost"], budget=80, cfg=SCREEN_1)
    assert [r.property_id for r in result] == ["B"]
    nr = result[0]
    assert nr.weak.factor == "travel"
    assert nr.weak.part == "timetable"
    assert nr.weak.value == 40
    assert nr.weak.top1_value == 3
    assert nr.message == "1위 매물보다 저렴하지만 시간표 기준 편도 평균 40분이라 희망 통학 시간(15분)보다 25분 길어요."


def test_weak_cost_message_over_budget():
    cheap = cand("A", travel=travel_score(15, department_avg_min=20), cost=100, monthly_cost=45)
    near = cand("B", travel=travel_score(15, department_avg_min=1), cost=0, monthly_cost=82, over_budget=True)
    nr = not_recommended([cheap, near], ("cost", "travel"), ["travel", "cost"], budget=75, cfg=SCREEN_1)[0]
    assert nr.property_id == "B"
    assert nr.weak.factor == "cost"
    assert nr.message == "1위 매물보다 가깝지만 월 고정 주거비가 82만 원으로 예산(75만 원)보다 7만 원 비싸요."


def test_null_factor_is_never_weak():
    # 요청 2: 점수가 없는(null) 요소는 약한 요소 후보에서 뺀다.
    # 1위는 교통 편의성 점수가 있지만 B 는 null → 교통 편의성 차이가 가장 커 보여도 고르지 않는다.
    top = cand("A", travel=travel_score(15, department_avg_min=5), cost=40, transit=100)
    b = cand("B", travel=travel_score(15, department_avg_min=10), cost=100, transit=None, monthly_cost=45)
    result = not_recommended([top, b], ("transit", "travel"), ["travel", "cost", "transit"], budget=80, cfg=SCREEN_1)
    assert [r.property_id for r in result] == ["B"]
    assert result[0].weak.factor != "transit"
    assert result[0].weak.factor == "travel"


def test_not_recommended_excludes_on_screen_and_respects_displayed_top_n():
    # 요청 4: "지금 화면에 있는 매물" = 현재 조합 상위 displayed_top_n 위 (설정값)
    for n in (1, 3, 5):
        cfg = replace(CONFIG, displayed_top_n=n)
        result = recommend(PROPS, req(), cfg=cfg)
        on_screen = set(ids(result.ranked[:n]))
        assert not on_screen & set(ids(result.not_recommended))


def test_not_recommended_must_be_top_k_in_another_combo():
    result = recommend(PROPS, req())
    assert result.not_recommended  # 실제 데이터에서도 나온다
    for nr in result.not_recommended:
        assert nr.best_rank_elsewhere <= CONFIG.not_recommended_top_k
        assert all(tuple(h.priorities) != ("travel", "cost") for h in nr.top_in)
        assert nr.message


def test_not_recommended_max_and_order_by_best_rank_then_id():
    # 요청 3: 3개를 넘으면 다른 조합에서의 최고 순위가 높은 순 → 동점이면 ID 순으로 3개
    # 실제 데이터 10개로는 후보가 3개를 넘지 않아, 요소마다 강한 매물을 직접 만든다 (교통 편의성 점수도 있다고 가정)
    cands = [
        cand("C1", travel_value=100, cost=0, transit=0),
        cand("C2", travel_value=0, cost=100, transit=0),
        cand("C3", travel_value=0, cost=0, transit=100),
        cand("C4", travel_value=90, cost=10, transit=0),
        cand("C5", travel_value=10, cost=90, transit=0),
        cand("C6", travel_value=0, cost=10, transit=90),
        cand("C7", travel_value=50, cost=50, transit=50),
    ]
    args = (cands, ("travel", "cost"), ["travel", "cost", "transit"], 80)
    everyone = not_recommended(*args, cfg=replace(CONFIG, displayed_top_n=1, not_recommended_max=99))
    assert len(everyone) > 3
    expected = sorted(everyone, key=lambda r: (r.best_rank_elsewhere, r.property_id))[:3]

    picked = not_recommended(*args, cfg=replace(CONFIG, displayed_top_n=1))
    assert len(picked) == 3
    assert ids(picked) == ids(expected)
    ranks = [(r.best_rank_elsewhere, r.property_id) for r in picked]
    assert ranks == sorted(ranks)


def test_top1_is_never_not_recommended():
    result = recommend(PROPS, req(), cfg=replace(CONFIG, displayed_top_n=0))
    assert result.ranked[0].property_id not in ids(result.not_recommended)
