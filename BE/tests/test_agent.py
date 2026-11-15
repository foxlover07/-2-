"""챗봇 흐름 (가짜 LLM, PoC 매물 10개). LLM 을 부르지 않는다.

LLM 이 정확히 뽑았다고 치고, 그 뒤의 서버 규칙(조건 반영·되묻기·제외 조건·응답 형식·거절)이
규칙 문서대로 돌아가는지 본다. LLM 자체의 정확도는 eval/cases 로 따로 잰다.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent import rules, tools
from app.agent.graph import build_graph, numbers_grounded, run_turn
from app.agent.llm import LLMError, ScriptedLLM, explain_system, extract_system, plan_system
from app.agent.schemas import AgentProfile, ConditionDraft, PlanDraft, ProfileChanges, SlotIn, TurnExtraction
from app.data import load_properties, load_travel_times

FULL = {"university": "이화여자대학교", "department_buildings": ["공과대학"], "priorities": ["travel", "cost"],
        "budget_manwon": 80, "desired_commute_min": 15, "desired_area_m2": 20}


def X(intent="update", **kw):
    changes = kw.pop("changes", {})
    return TurnExtraction(intent=intent, changes=ProfileChanges(**changes), **kw)


def graph_with(llm):
    def data():
        props = load_properties()
        return props, load_travel_times(props)
    return build_graph(lambda: llm, data)


def turn(g, msg, profile=None, sid="s1"):
    return run_turn(g, sid, msg, profile)


# ---------------------------------------------------------------- 조건 반영 (서버 규칙)

def test_pyeong_converted_by_server():
    p, notes, _ = tools.apply_changes(AgentProfile(), ProfileChanges(area_value=6, area_unit="평"))
    assert p.desired_area_m2 == pytest.approx(19.83, abs=0.01)
    assert any("19.83㎡" in n for n in notes)


def test_delta_and_priorities_rule():
    cur = AgentProfile(**FULL)
    p, _, _ = tools.apply_changes(cur, ProfileChanges(budget_delta_manwon=10, commute_delta_min=-5))
    assert (p.budget_manwon, p.desired_commute_min) == (90, 10)
    # R-306: 1순위만 말하면 기존 1순위가 2순위로
    p, _, _ = tools.apply_changes(cur.model_copy(update={"priorities": ["cost", "area"]}), ProfileChanges(priority_first="travel"))
    assert p.priorities == ["travel", "cost"]
    p, _, _ = tools.apply_changes(cur, ProfileChanges(priority_first="travel"))
    assert p.priorities == ["travel", "cost"]  # 이미 1순위면 2순위 유지
    p, _, _ = tools.apply_changes(AgentProfile(), ProfileChanges(priority_first="travel"))
    assert p.priorities == ["travel"] and "priorities.second" in tools.missing_fields(p)


def test_school_change_clears_buildings_and_timetable():
    cur = AgentProfile(**FULL, timetable=[{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"}])
    p, notes, _ = tools.apply_changes(cur, ProfileChanges(university="고대"))
    assert p.university == "고려대학교" and p.department_buildings == [] and p.timetable == []
    assert "department_buildings" in tools.missing_fields(p)


def test_unknown_building_not_invented():
    cur = AgentProfile(**FULL)
    p, notes, _ = tools.apply_changes(cur, ProfileChanges(add_department_buildings=["우주과학관"]))
    assert p.department_buildings == ["공과대학"]
    assert "ECC, 공과대학, 이화·포스코관, 학관" in notes[0]
    p, _, _ = tools.apply_changes(cur, ProfileChanges(department_buildings=["포관"]))
    assert p.department_buildings == ["이화·포스코관"]


def test_transit_time_asks_walking_minutes():
    cur = AgentProfile(**FULL)
    p, notes, asks = tools.apply_changes(cur, ProfileChanges(commute_not_walking=True, desired_commute_min=20))
    assert p.desired_commute_min == 15 and asks == ["desired_commute_min"] and "도보" in notes[0]


def test_timetable_needs_all_four_fields():
    cur = AgentProfile(**FULL)
    p, _, asks = tools.apply_changes(cur, ProfileChanges(timetable_add=[SlotIn(day="월", start="09:00", end="10:30")]))
    assert p.timetable == [] and asks == ["timetable.building"]
    p, _, asks = tools.apply_changes(cur, ProfileChanges(timetable_add=[
        SlotIn(day="월", start="09:00", end="10:30", building="ECC"), SlotIn(day="수", start="09:00", end="10:30", building="ecc")]))
    assert len(p.timetable) == 2 and asks == []


def test_questions_two_at_a_time_with_examples():
    keys = tools.questions(AgentProfile())
    assert keys == ["university", "department_buildings"]
    text = tools.question_text(AgentProfile(university="이화여자대학교"), ["desired_commute_min", "desired_area_m2"])
    assert "15분" in text and "6평" in text


# ---------------------------------------------------------------- 흐름

def test_ask_then_recommend_flow():
    llm = ScriptedLLM([
        X("recommend"),
        X("update", changes={"university": "이대", "department_buildings": ["공대"]}),
        X("update", changes={"priorities": ["travel", "cost"]}),
        X("update", changes={"budget_manwon": 80, "desired_commute_min": 15, "area_value": 6, "area_unit": "평"}),
    ], explanations=["1위 매물이 가장 가까워요."])
    g = graph_with(llm)
    s = turn(g, "집 추천해 줘")
    assert s["status"] == "need_info" and s["asks"] == ["university", "department_buildings"]
    assert "학교" in s["response"] and not s["recommended"]
    s = turn(g, "이화여대 공대야")
    assert s["asks"] == ["priorities", "budget_manwon"] and s["response"].startswith("바뀐 것:")
    s = turn(g, "통학 1순위, 예산 2순위")
    s = turn(g, "월 80, 15분, 6평")
    assert s["status"] == "ok" and s["recommended"]
    r = s["response"]
    assert r.startswith("바뀐 것:") and "1위 매물이 가장 가까워요." in r
    assert "넓은 면적은 상관없음" in r and "원문" in r
    assert numbers_grounded(r, s["facts"])


def test_greeting_asks_more_conditions_or_required():
    g = graph_with(ScriptedLLM([X("greeting"), X("greeting")]))
    s = turn(g, "안녕!", FULL)
    assert "안녕" in s["response"] and "조건" in s["response"] and not s["recommended"]
    s = turn(g, "안녕", None, sid="new")
    assert "학교" in s["response"]


@pytest.mark.parametrize("topic,must", [
    ("fraud_registry", "인터넷등기소"), ("contract_legal", "132"), ("market_forecast", "예측해 드리지 않아요"),
    ("unsupported_region", "서강대학교"), ("unrelated", "자취방"), ("timetable_photo", "직접"),
])
def test_refusals_are_templates(topic, must):
    g = graph_with(ScriptedLLM([X("out_of_scope", scope_topic=topic)]))
    s = turn(g, "질문", FULL)
    assert must in s["response"] and not s["profile_changed"] and not s["recommended"]
    for bad in ("안전합니다", "사기가 아닙니다", "계약해도 됩니다"):
        assert bad not in s["response"]


def test_unknown_school_becomes_region_refusal():
    g = graph_with(ScriptedLLM([X("update", changes={"university": "부산대"})]))
    s = turn(g, "부산대 근처도 추천해 줘", FULL)
    assert s["intent"] == "out_of_scope" and "이화여자대학교" in s["response"]
    assert AgentProfile.model_validate(s["current_profile"]).university == "이화여자대학교"


def test_timetable_photo_request_by_keyword():
    g = graph_with(ScriptedLLM([X("explain")]))
    s = turn(g, "시간표 사진 올리면 넣어 줘", FULL)
    assert "직접" in s["response"]


def test_what_if_keeps_profile():
    g = graph_with(ScriptedLLM([X("what_if", changes={"budget_delta_manwon": 10})], explanations=["가정 결과예요."]))
    s = turn(g, "예산 10만 올리면?", FULL)
    assert not s["profile_changed"] and AgentProfile.model_validate(s["current_profile"]).budget_manwon == 80
    assert AgentProfile.model_validate(s["scenario_profile"]).budget_manwon == 90 and s["comparison"]


def test_update_then_revert():
    llm = ScriptedLLM([X("update", changes={"budget_manwon": 90}), X("revert")], explanations=["a", "b"])
    g = graph_with(llm)
    s = turn(g, "예산 90으로 바꿔 줘", FULL)
    assert s["response"].startswith("바뀐 것:") and "80만 원 → 90만 원" in s["response"]
    s = turn(g, "아까 조건으로 돌려 줘")
    assert AgentProfile.model_validate(s["current_profile"]).budget_manwon == 80


def test_number_check_ignores_llm_text_itself():
    g = graph_with(ScriptedLLM([X("recommend")], explanations=["숫자 없는 요약이에요."]))
    s = turn(g, "추천해 줘", FULL)
    assert "숫자 없는 요약" not in s["facts"]["code_text"] and not numbers_grounded("월 98765만 원", s["facts"])


def test_ungrounded_number_falls_back_to_facts():
    g = graph_with(ScriptedLLM([X("recommend")], explanations=["1위는 월 12345만 원이에요."]))
    s = turn(g, "추천해 줘", FULL)
    assert "12345" not in s["response"] and s["trace"]["explanation_source"] == "fallback_ungrounded"


def test_explain_failure_uses_fact_summary():
    g = graph_with(ScriptedLLM([X("recommend")], explanations=[LLMError("x", "busy")]))
    s = turn(g, "추천해 줘", FULL)
    assert "1위" in s["response"] and numbers_grounded(s["response"], s["facts"])


def test_llm_error_message():
    g = graph_with(ScriptedLLM([LLMError("x", "quota")]))
    s = turn(g, "추천해 줘", FULL)
    assert s["status"] == "llm_error" and "한도" in s["response"]


# ---------------------------------------------------------------- 제외 조건 (5장)

BAR = ConditionDraft(tool="amenity", label="술집", amenity="bar", metric="nearest_m", op="<=", value=30)


def test_exclusion_applied_without_confirmation():
    g = graph_with(ScriptedLLM([X("exclude", exclusion_request="밤에 시끄러운 곳 싫어")],
                               explanations=["요약"], plans=[PlanDraft(conditions=[BAR])]))
    s = turn(g, "밤에 시끄러운 곳 싫어", FULL)
    p = AgentProfile.model_validate(s["current_profile"])
    assert s["exclusion_applied"] and len(tools.all_conditions(p.exclusions)) == 1
    r = s["response"]
    assert r.startswith("바뀐 것:") and "30m" in r and "조건 빼 줘" in r and s["recommended"]


def test_mixed_update_and_exclusion():
    floor = ConditionDraft(tool="listing_field", label="층 종류", field="floor_kind", op="in", values=["반지하", "지하"])
    g = graph_with(ScriptedLLM([X("update", changes={"budget_manwon": 90}, exclusion_request="반지하는 싫어")],
                               explanations=["요약"], plans=[PlanDraft(conditions=[floor])]))
    s = turn(g, "예산 90으로 하고 반지하는 싫어", FULL)
    r = s["response"]
    assert r.startswith("바뀐 것:\n- 유지비 상한") and "반지하" in r
    p = AgentProfile.model_validate(s["current_profile"])
    assert p.budget_manwon == 90 and len(p.exclusions) == 1


def test_onsite_only_is_not_stored():
    g = graph_with(ScriptedLLM([X("exclude", exclusion_request="방음 잘 되는 집")],
                               plans=[PlanDraft(onsite_checks=["방음"])]))
    s = turn(g, "방음 잘 되는 집이면 좋겠어", FULL)
    assert AgentProfile.model_validate(s["current_profile"]).exclusions == []
    assert s["response"].startswith("바뀐 것: 없음") and "두드려" in s["response"] and not s["recommended"]


def test_onsite_word_condition_is_blocked_by_server():
    """LLM 이 방음을 건물 연식으로 대신 판단하려 해도 서버가 막는다 (R-503)."""
    bad = ConditionDraft(tool="building_field", label="방음", field="building_age_years", op=">", value=20)
    plan, _ = tools.plan_from_draft(PlanDraft(conditions=[bad]), "방음", None)
    assert plan.conditions == [] and plan.onsite_checks == ["방음"]


def test_zero_left_is_not_applied():
    everything = ConditionDraft(tool="listing_field", label="매물 유형", field="property_type", op="in", values=["원룸", "오피스텔"])
    g = graph_with(ScriptedLLM([X("exclude", exclusion_request="원룸도 오피스텔도 싫어")], plans=[PlanDraft(conditions=[everything])]))
    s = turn(g, "원룸도 오피스텔도 싫어", FULL)
    assert s["exclusion_blocked"] and not s["exclusion_applied"]
    assert AgentProfile.model_validate(s["current_profile"]).exclusions == []
    assert "적용하지 않았어요" in s["response"] and "?" in s["response"]


def test_same_target_replaces_condition_and_remove():
    near = lambda v: ConditionDraft(tool="amenity", label="편의점", amenity="convenience", metric="nearest_m", op=">", value=v)  # noqa: E731
    llm = ScriptedLLM([X("exclude", exclusion_request="편의점 가까웠으면"), X("exclude", exclusion_request="편의점 100m로"),
                       X("remove_exclusion", remove_exclusion="편의점")],
                      explanations=["a", "b", "c"], plans=[PlanDraft(conditions=[near(200)]), PlanDraft(conditions=[near(100)])])
    g = graph_with(llm)
    turn(g, "편의점 가까웠으면 좋겠어", FULL)
    s = turn(g, "편의점 조건 100m로 바꿔 줘")
    conds = tools.all_conditions(AgentProfile.model_validate(s["current_profile"]).exclusions)
    assert [c.exclude_if.value for c in conds] == [100] and "새 기준" in s["response"]
    s = turn(g, "편의점 조건 빼 줘")
    assert AgentProfile.model_validate(s["current_profile"]).exclusions == []
    assert s["response"].startswith("바뀐 것:") and "편의점" in s["response"]


def test_missing_amenity_column_goes_to_kakao():
    gym = ConditionDraft(tool="amenity", label="헬스장", amenity="gym", metric="nearest_m", op=">", value=200)
    plan, _ = tools.plan_from_draft(PlanDraft(conditions=[gym]), "헬스장", load_properties())
    assert plan.conditions[0].tool == "nearest_distance" and plan.conditions[0].keywords == ["헬스장"]


def test_profile_field_condition_is_not_exclusion():
    dep = ConditionDraft(tool="listing_field", label="보증금", field="deposit_manwon", op=">", value=500)
    plan, notes = tools.plan_from_draft(PlanDraft(conditions=[dep]), "보증금 500 넘으면", None)
    assert plan.conditions == [] and "보증금 상한" in notes[0]


# ---------------------------------------------------------------- 프롬프트·API

def test_prompts_come_from_rules_doc():
    assert "R-201" in extract_system() and "R-701" in extract_system() and "**R-508 " not in extract_system()
    assert "R-508" in plan_system() and "R-801" in explain_system()
    assert rules.buildings_by_university()["고려대학교"][2] == "신공학관"


def test_chat_api(monkeypatch):
    from app.main import app
    from app.routers import agent as router

    llm = ScriptedLLM([X("greeting")])
    monkeypatch.setattr(router, "_llm", lambda: llm)
    body = {"session_id": "api", "message": "안녕", "profile": FULL}
    res = TestClient(app).post("/agent/chat", json=body)
    assert res.status_code == 200
    out = res.json()
    assert out["intent"] == "greeting" and out["status"] == "ok" and "안녕" in out["assistant_message"]
