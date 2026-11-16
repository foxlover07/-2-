"""의사결정 지원 분석 (app.decision, /analysis/*) — Phase 2.

팀 PoC 데이터(DB/poc_ewha.csv, 이대 10건)로 돈다 (conftest). 엔진 결과를 바꾸지 않는지,
분석 값이 정의대로 계산되는지 확인한다.
"""

import pytest
from fastapi.testclient import TestClient

from app.data import load_properties, load_travel_times
from app.decision.engine import EngineContext, travel_min
from app.decision.pareto import _key, dominates, pareto_analysis, tradeoff_analysis
from app.decision.relaxation import BUDGET_STEPS, RelaxationRequest, run_relaxation
from app.decision.sensitivity import SensitivityRequest, kendall_tau, run_sensitivity, spearman_rho
from app.main import app
from app.scoring import RecommendRequest, recommend


@pytest.fixture
def req() -> RecommendRequest:
    return RecommendRequest(
        department="컴퓨터공학전공", grade=2, priorities=("travel", "cost"), budget_manwon=75,
        deposit_budget_manwon=1000, desired_commute_min=15, desired_area_m2=20,
    )


def _engine(req):
    props = list(load_properties())
    return recommend(props, req, travel=load_travel_times(props))


# ---------------------------------------------------------------- 엔진 결과 보존

def test_analyses_do_not_change_engine_result(req):
    before = _engine(req).model_dump()
    snapshot = req.model_dump()
    ctx = EngineContext()
    pareto_analysis(ctx.run(req).ranked)
    run_sensitivity(SensitivityRequest(request=req), ctx)
    run_relaxation(RelaxationRequest(request=req), ctx)
    assert _engine(req).model_dump() == before   # 같은 입력이면 엔진 결과 그대로
    assert req.model_dump() == snapshot           # 요청(사용자 조건)도 그대로


def test_travel_min_matches_target_plus_gap(req):
    for r in _engine(req).ranked:
        assert travel_min(r) == pytest.approx(15 + r.vs_target.travel_min, abs=0.05)


# ---------------------------------------------------------------- Pareto

def test_dominates():
    assert dominates((1, 2), (1, 3)) and not dominates((1, 3), (1, 3)) and not dominates((0, 4), (1, 3))


def test_pareto_frontier_is_non_dominated_and_dominated_have_witness(req):
    ranked = _engine(req).ranked
    res = pareto_analysis(ranked)
    keys = {r.property_id: _key(r, res.objectives) for r in ranked}
    front = {i.property_id for i in res.frontier}
    for a in front:
        assert not any(dominates(keys[b], keys[a]) for b in keys if b != a)
    assert res.dominated_count == len(ranked) - len(front)
    # 두 목적(유지비·면적)만 보면 지배되는 매물이 생긴다 → 지배한 매물이 실제로 지배하는지
    two = pareto_analysis(ranked, ["cost", "area"], top_k=len(ranked))
    k2 = {r.property_id: _key(r, ["cost", "area"]) for r in ranked}
    for item in two.top_k:
        if not item.pareto:
            assert dominates(k2[item.dominated_by], k2[item.property_id])
    assert [i.monthly_cost_manwon for i in two.frontier] == sorted(i.monthly_cost_manwon for i in two.frontier)


def test_pareto_marks_unsupported_objectives(req):
    res = pareto_analysis(_engine(req).ranked, ["cost", "gap_home", "travel", "nope"])
    assert res.objectives == ["cost", "travel"]
    assert "gap_home" in res.skipped_objectives and res.skipped_objectives["nope"] == "알 수 없는 목적"


# ---------------------------------------------------------------- Trade-off

def test_tradeoff_values_come_from_real_candidates(req):
    ranked = _engine(req).ranked
    t = tradeoff_analysis(ranked)
    ref = ranked[0]
    assert t.reference["property_id"] == ref.property_id
    cheapest = min(ranked, key=lambda r: (r.monthly_cost_manwon, r.rank))
    assert t.cheapest["property_id"] == cheapest.property_id
    assert t.cheapest_vs_reference.monthly_cost_manwon == pytest.approx(cheapest.monthly_cost_manwon - ref.monthly_cost_manwon)
    # 더 낼 수 있는 금액이 커지면 찾을 수 있는 최단 통학은 같거나 짧아진다
    walks = [s.best["travel_min"] for s in t.pay_more_for_commute if s.best]
    assert walks == sorted(walks, reverse=True)
    for s in t.smaller_area_cheaper:
        if s.best:
            assert s.best["monthly_cost_manwon"] < ref.monthly_cost_manwon and s.saved_manwon > 0
    assert tradeoff_analysis([]) is None


# ---------------------------------------------------------------- 민감도

def test_rank_correlations():
    a = ["a", "b", "c", "d"]
    assert kendall_tau(a, a) == 1.0 and spearman_rho(a, a) == 1.0
    assert kendall_tau(a, a[::-1]) == -1.0 and spearman_rho(a, a[::-1]) == -1.0
    assert kendall_tau(a, ["b", "a", "c", "d"]) == pytest.approx(4 / 6, abs=1e-4)
    assert kendall_tau(["a"], ["a"]) is None
    assert kendall_tau(a, ["x", "c", "a"]) == -1.0  # 공통 매물(a, c)만 비교


def test_sensitivity_scenarios_and_stable_counts(req):
    res = run_sensitivity(SensitivityRequest(request=req))
    kinds = [s.scenario.kind for s in res.scenarios]
    assert kinds.count("priority") == 5 and kinds.count("weights") == 5 and kinds.count("budget") == 4
    assert "timetable" not in kinds  # 시간표가 없으면 그 시나리오도 없다
    assert res.engine_calls == len(res.scenarios) + 1
    for s in res.scenarios:
        assert 0 <= s.top_retention <= 1 and len(s.top) <= res.top_k
    for c in res.stable:
        assert 1 <= c.in_top <= c.total == len(res.scenarios) + 1
    assert res.stable[0].in_top >= res.stable[-1].in_top
    rets = [k.mean_top_retention for k in res.by_kind]
    assert rets == sorted(rets)  # 민감한 조건(유지율 낮음)부터
    assert "확률이 아닙니다" in res.notes[0]


def test_sensitivity_budget_increase_never_removes_candidates(req):
    res = run_sensitivity(SensitivityRequest(request=req, kinds=["budget"]))
    by = {s.scenario.key: s.candidates for s in res.scenarios}
    assert by["budget:-10"] <= by["budget:-5"] <= res.baseline_candidates <= by["budget:+5"] <= by["budget:+10"]


# ---------------------------------------------------------------- 조건 완화

def test_relaxation_finds_minimal_budget_and_respects_policy(req):
    tight = req.model_copy(update={"budget_manwon": 40})
    res = run_relaxation(RelaxationRequest(request=tight, target_count=3))
    assert res.needs_relaxation and res.baseline_candidates < 3
    assert res.failures.by_stage.get("budget", 0) > 0
    budget_opts = [o for o in res.options if o.lever == "budget"]
    assert [o.magnitude for o in budget_opts] == list(range(1, len(BUDGET_STEPS) + 1))
    counts = [o.candidates for o in budget_opts]
    assert counts == sorted(counts)  # 상한을 올릴수록 후보는 줄지 않는다
    m = next(o for o in res.minimal if o.lever == "budget")
    assert m.reaches_target and all(not o.reaches_target for o in budget_opts if o.magnitude < m.magnitude)
    assert all(o.hypothetical for o in res.options)
    # 완화한 상한으로 엔진을 직접 돌린 후보 수와 같다 (필터 정책 그대로)
    assert m.candidates == len(_engine(tight.model_copy(update={"budget_manwon": m.change["budget_manwon"][1]})).ranked)


def test_relaxation_skips_locked_levers(req):
    tight = req.model_copy(update={"budget_manwon": 40})
    res = run_relaxation(RelaxationRequest(request=tight, locked=["budget"]))
    assert not any(o.lever == "budget" for o in res.options) and res.locked == ["budget"]
    assert any("바꾸지 않기로 한 조건" in n for n in res.notes)


def test_relaxation_timetable_lever_only_when_timetable(req):
    res = run_relaxation(RelaxationRequest(request=req))
    assert not any(o.lever == "timetable" for o in res.options)
    with_tt = RecommendRequest.model_validate(
        req.model_dump() | {"timetable": [{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"}]})
    res2 = run_relaxation(RelaxationRequest(request=with_tt))
    assert any(o.lever == "timetable" and o.change == {"timetable": "none"} for o in res2.options)


# ---------------------------------------------------------------- API

def test_analysis_api(req):
    cl = TestClient(app)
    body = {"request": req.model_dump(mode="json")}
    for path in ("pareto", "tradeoff", "sensitivity", "relaxation"):
        r = cl.post(f"/analysis/{path}", json=body)
        assert r.status_code == 200, (path, r.text)
    bad = req.model_dump(mode="json") | {"department_buildings": ["우주과학관"]}
    assert cl.post("/analysis/pareto", json={"request": bad}).status_code == 422
