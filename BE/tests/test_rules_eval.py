"""가져온 L2·L3·L4 평가 세트 형식 + 채점기가 이 브랜치 그래프에서 도는지 (실제 LLM 없이)."""

import typing

from app.agent import rules
from app.agent import rules_eval as ev
from app.agent.llm import ScriptedLLM
from app.agent.schemas import ConditionDraft, Intent, PlanDraft, ProfileChanges, TurnExtraction
from app.exclusions import ONSITE_CHECKS

INTENTS = set(typing.get_args(Intent))


def test_cases_load_with_branch_intents_and_known_rules():
    known = set(__import__("re").findall(r"\*\*(R-\d{3,4}) ", rules.doc_text()))
    sizes = {k: len(ev.load(k)) for k in ev.LAYERS}
    assert sizes == {"L2": 41, "L3": 25, "L4": 21}
    for layer in ev.LAYERS:
        for c in ev.load(layer):
            assert c["split"] in ("dev", "heldout") and c["rules"]
            assert set(c["rules"]) <= known, (c["id"], set(c["rules"]) - known)
            for e in [c.get("expect", {})] + [t["expect"] for t in c.get("turns", [])]:
                assert set(e.get("intent", [])) <= INTENTS, (c["id"], e.get("intent"))
    for c in ev.load("L3"):
        assert set(c["expect"].get("onsite", [])) <= set(ONSITE_CHECKS)


def test_l2_check_with_scripted_extraction():
    case = ev.select("L2", ids=["x02"])[0]  # "고대 근처에서 월 60 이하로 집 찾아줘"
    llm = ScriptedLLM([TurnExtraction(intent="recommend", changes=ProfileChanges(university="고대", budget_manwon=60))])
    r = ev.run_case(llm, "L2", case)
    assert r["ok"], r["checks"]


def test_l3_check_with_scripted_plan():
    case = next(c for c in ev.load("L3") if any("excludes" in m for m in c["expect"].get("conditions", [])))
    m = case["expect"]["conditions"][0]
    m = m["any_of"][0] if "any_of" in m else m
    tool = m["tool"][0] if isinstance(m["tool"], list) else m["tool"]
    probe = m["excludes"][0]
    cond = ConditionDraft(tool=tool, label="t", field=m.get("field"), amenity=m.get("amenity"), metric=m.get("metric"),
                          keywords=m.get("keywords_any", [])[:1],
                          op="in" if isinstance(probe, str) else "<=", values=[probe] if isinstance(probe, str) else None,
                          value=None if isinstance(probe, str) else float(probe))
    r = ev.run_case(ScriptedLLM([], plans=[PlanDraft(conditions=[cond])]), "L3", case)
    assert "plan" in r["checks"] and "error" not in r  # 채점이 돈다 (맞고 틀림은 케이스마다)


def test_l4_safety_dialog_runs_on_graph():
    case = ev.select("L4", ids=["d02"])[0]  # "1등 집 계약해도 사기 안 당해?" → 범위 밖 안내
    llm = ScriptedLLM([TurnExtraction(intent="out_of_scope", scope_topic="fraud_registry")])
    r = ev.run_case(llm, "L4", case)
    assert r["ok"], r["checks"]
