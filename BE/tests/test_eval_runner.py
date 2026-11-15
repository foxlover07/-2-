"""평가 실행기 판정이 맞는지: '정답만 내는 가짜 LLM'이면 모든 케이스가 통과해야 한다.

통과하지 못하는 케이스는 케이스 기대값과 서버 규칙이 어긋난 것이다 (LLM 탓이 아님).
"""

import json

import pytest

from app.agent.llm import LLMError, ScriptedLLM
from app.agent.schemas import ConditionDraft, PlanDraft, ProfileChanges, SlotIn, TurnExtraction
from app.data import load_properties, load_travel_times
from eval import runner
from eval.cases_check import load


def X(intent, **kw):
    return TurnExtraction(intent=intent, changes=ProfileChanges(**kw.pop("c", {})), **kw)


S = lambda d, s, e, b=None: SlotIn(day=d, start=s, end=e, building=b)  # noqa: E731

# L2 정답 (사람이 쓴 추출 결과)
L2_ORACLE = {
    "x01": X("recommend", c=dict(university="이화여대", department="공대", department_buildings=["공대"], budget_manwon=70,
                                 priority_first="travel")),
    "x02": X("recommend", c=dict(university="고대", budget_manwon=60)),
    "x03": X("update", c=dict(deposit_budget_manwon=1000, budget_manwon=75)),
    "x04": X("recommend", c=dict(budget_manwon=70)),
    "x06": X("what_if", c=dict(budget_delta_manwon=10)),
    "x08": X("what_if", c=dict(university="고려대")),
    "x09": X("update", c=dict(university="고려대")),
    "x10": X("update", c=dict(priorities=["cost", "travel"])),
    "x11": X("update", c=dict(priority_second="area")),
    "x12": X("update", c=dict(budget_manwon=70)),
    "x14": X("compare"),
    "x15": X("explain", property_rank=2),
    "x17": X("greeting"),
    "x18": X("update", unsupported=["홍대 알바 거리"]),
    "x19": X("exclude", exclusion_request="편의점 가까운 곳이 좋아"),
    "x20": X("exclude", exclusion_request="밤에 안전한 동네"),
    "x21": X("update", c=dict(add_department_buildings=["포관"])),
    "x22": X("update", c=dict(add_department_buildings=["우주과학관"])),
    "x23": X("update", c=dict(timetable_add=[S("월", "09:00", "10:15", "공과대학"), S("월", "13:00", "14:15", "ECC")])),
    "x24": X("update", c=dict(timetable_add=[S("월", "09:00", "10:30"), S("수", "09:00", "10:30")])),
    "x26": X("update", c=dict(commute_not_walking=True)),
    "x27": X("what_if", c=dict(budget_delta_manwon=-5)),
    "x28": X("update", c=dict(budget_manwon=90, priority_first="area")),
    "x29": X("recommend", c=dict(university="서강대", budget_manwon=70, priority_first="travel")),
    "x30": X("update", c=dict(priorities=["travel", "cost"])),
    "x31": X("recommend", c=dict(university="과기대", deposit_budget_manwon=500, budget_manwon=50)),
    "n01": X("update", c=dict(priority_first="travel")),
    "n03": X("update", c=dict(area_value=6, area_unit="평")),
    "n05": X("update", c=dict(budget_delta_manwon=10)),
    "n07": X("update"),
    "n08": X("update", c=dict(budget_manwon=80), exclusion_request="반지하는 싫어"),
    "n09": X("update", c=dict(deposit_budget_manwon=500)),
    "n10": X("update", c=dict(area_value=20, area_unit="㎡")),
    "n11": X("update", c=dict(desired_commute_min=15)),
    "n12": X("update", c=dict(larger_area="like")),
    "n14": X("update", c=dict(add_department_buildings=["신공학관"])),
    "n15": X("update", c=dict(desired_commute_min=15, area_value=6, area_unit="평")),
    "n16": X("recommend"),
    "n18": X("remove_exclusion", remove_exclusion="술집"),
    "n19": X("out_of_scope", scope_topic="fraud_registry"),
    "n20": X("update", c=dict(university="부산대")),
}


def test_l2_oracle_covers_every_case():
    assert set(L2_ORACLE) == {c["id"] for c in load("l2_extract.jsonl")}


@pytest.mark.parametrize("case", load("l2_extract.jsonl"), ids=lambda c: c["id"])
def test_l2_oracle_passes(case):
    checks = runner.check_l2(case, L2_ORACLE[case["id"]])
    assert checks and all(checks.values()), {k: v for k, v in checks.items() if not v}


def _draft(ref: dict) -> PlanDraft:
    conds = []
    for c in ref.get("conditions", []):
        ei = c["exclude_if"]
        conds.append(ConditionDraft(
            tool=c["tool"], label=c["label"], field=c.get("field"), amenity=c.get("amenity"), metric=c.get("metric"),
            keywords=c.get("keywords", []), radius_m=c.get("radius_m"), op=ei["op"],
            value=None if isinstance(ei["value"], list) else ei["value"],
            values=ei["value"] if isinstance(ei["value"], list) else None))
    return PlanDraft(conditions=conds, unsupported=ref.get("unsupported", []), onsite_checks=ref.get("onsite_checks", []))


@pytest.mark.parametrize("case", load("l3_plan.jsonl"), ids=lambda c: c["id"])
def test_l3_oracle_passes(case):
    # 실제 통합 매물 데이터처럼 주변 시설 칸이 있다고 보고(매물 목록 없음 = 시설 전부) 판정한다.
    # PoC 데이터에는 주변 시설 칸이 없어 amenity 가 카카오로 바뀌는 것은 test_agent 에서 따로 본다.
    llm = ScriptedLLM([], plans=[_draft(case["reference"])])
    res = runner.run_l3(llm, case, [])
    assert res["checks"]["plan"], res["why"]


def test_paced_spaces_calls_and_stops_on_quota():
    t = [0.0]
    slept: list[float] = []
    llm = ScriptedLLM([X("greeting"), LLMError("한도", "quota")])
    p = runner.Paced(llm, rpm=12, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    p.extract("a", {})
    with pytest.raises(runner.QuotaStop):
        p.extract("b", {})
    assert slept == [5.0]


def test_run_writes_rows_and_summary(tmp_path):
    props = load_properties()
    data = lambda: (props, load_travel_times(props))  # noqa: E731
    out = tmp_path / "r.jsonl"
    case_ids = ["x15", "x17"]
    llm = ScriptedLLM([L2_ORACLE[i] for i in case_ids])
    assert runner.run(llm, ["L2"], out, ids=case_ids, data=data, log=lambda s: None)
    rows = runner.load_rows(out)
    assert [r["ok"] for r in rows] == [True, True]
    md = runner.summary_md(rows, {"label": "t"}, before=rows)
    assert "| L2 | 전체 | 2/2 (100%)" in md and "1차 → 2차" in md
    assert "이화" not in md  # 요약 표에는 매물·답 원문을 넣지 않는다
    # 이어 하기: 한도에 걸리면 False, 다시 돌리면 남은 것만
    out2 = tmp_path / "q.jsonl"
    assert not runner.run(ScriptedLLM([L2_ORACLE["x15"], LLMError("q", "quota")]) if False else
                          runner.Paced(ScriptedLLM([L2_ORACLE["x15"], LLMError("q", "quota")]), rpm=0),
                          ["L2"], out2, ids=case_ids, data=data, log=lambda s: None)
    assert [r["id"] for r in runner.load_rows(out2)] == ["x15"]
    json.loads(out2.read_text(encoding="utf-8").splitlines()[0])


BAR30 = ConditionDraft(tool="amenity", label="술집", amenity="bar", metric="nearest_m", op="<=", value=30)
L4_ORACLE = {  # id → (추출들, 계획들)
    "d01": ([X("recommend"), X("update", c=dict(university="이대", department_buildings=["공대"])),
             X("update", c=dict(priorities=["travel", "cost"])),
             X("update", c=dict(budget_manwon=70, desired_commute_min=15, area_value=6, area_unit="평"))], []),
    "d02": ([X("out_of_scope", scope_topic="fraud_registry")], []),
    "d03": ([X("out_of_scope", scope_topic="contract_legal")], []),
    "d04": ([X("update", c=dict(university="부산대"))], []),
    "d05": ([X("out_of_scope", scope_topic="market_forecast")], []),
    "d06": ([X("out_of_scope", scope_topic="unrelated")], []),
    "d07": ([X("exclude", exclusion_request="밤에 시끄러운 곳 싫어")], [PlanDraft(conditions=[BAR30])]),
    "d08": ([X("exclude", exclusion_request="술집이 400m 안에 있으면 빼 줘")],
            [PlanDraft(conditions=[BAR30.model_copy(update={"value": 400})])]),
    "d09": ([X("remove_exclusion", remove_exclusion="술집")], []),
    "d10": ([X("update", c=dict(budget_manwon=80)), X("revert")], []),
    "d11": ([X("explain")], []),
    "d12": ([X("recommend")], []),
    "d13": ([X("exclude", exclusion_request="방음 잘 되는 집")], [PlanDraft(onsite_checks=["방음"])]),
    "d14": ([X("update", c=dict(budget_manwon=80), exclusion_request="반지하는 싫어")],
            [PlanDraft(conditions=[ConditionDraft(tool="listing_field", label="층 종류", field="floor_kind", op="in",
                                                  values=["반지하", "지하", "옥탑"])])]),
    "d15": ([X("recommend"), X("update", c=dict(desired_commute_min=15, area_value=6, area_unit="평"))], []),
    "d16": ([X("what_if", c=dict(budget_delta_manwon=10))], []),
    "d17": ([X("out_of_scope", scope_topic="unrelated")], []),
    "d18": ([X("update", c=dict(timetable_add=[S("월", "09:00", "10:30", "ECC"), S("수", "09:00", "10:30", "ECC")]))], []),
    "d19": ([X("explain")], []),
    "d20": ([X("greeting")], []),
    "d21": ([X("greeting")], []),
}


def test_l4_oracle_covers_every_case():
    assert set(L4_ORACLE) == {c["id"] for c in load("l4_dialog.jsonl")}


@pytest.mark.parametrize("case", load("l4_dialog.jsonl"), ids=lambda c: c["id"])
def test_l4_oracle_passes(case):
    props = load_properties()
    data = lambda: (props, load_travel_times(props))  # noqa: E731
    ex, plans = L4_ORACLE[case["id"]]
    llm = ScriptedLLM(ex, explanations=["조건에 맞는 매물을 정리했어요."] * 5, plans=plans)
    res = runner.run_l4(llm, case, data)
    bad = {k: v for k, v in res["checks"].items() if not v}
    assert res["checks"] and not bad, (bad, [o["response"] for o in res["output"]])
