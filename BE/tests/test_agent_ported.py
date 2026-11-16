"""agent-integrated 에서 가져온 것 (2026-11-16 '따올 것만'): 제외 계획 단계 · 범위 밖 안내 · 금지 문구 · 규칙 문서.

실제 LLM 은 부르지 않는다 (ScriptedLLM 의 plans 큐로 계획을 대신 준다).
"""

import uuid

from app.agent import rules, tools
from app.agent.graph import FORBIDDEN_PHRASES, REFUSAL, build_graph
from app.agent.llm import LLMError, ScriptedLLM, extract_system, plan_system
from app.agent.schemas import ConditionDraft, PlanDraft, ProfileChanges, TurnExtraction

FULL = dict(university="이대", budget_manwon=70, department="공대", department_buildings=["공대"],
            priorities=["travel", "cost"], desired_commute_min=15, desired_area_m2=18)


def X(intent, request=None, topic=None, **changes):
    return TurnExtraction(intent=intent, changes=ProfileChanges(**changes), exclusion_request=request, scope_topic=topic)


class Chat:
    def __init__(self, extractions, explanations=None, plans=None):
        self.llm = ScriptedLLM(extractions, explanations, plans)
        self.graph = build_graph(lambda: self.llm)
        self.cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    def say(self, msg, mode=None):
        return self.graph.invoke({"user_message": msg, "session_id": "t", "fe_profile": None, "chat_mode": mode},
                                 config=self.cfg)


FLOOR = PlanDraft(conditions=[ConditionDraft(tool="listing_field", label="층 종류", field="floor_kind", op="in",
                                             values=["반지하", "지하", "옥탑"])])


# ---------------------------------------------------------------- 제외 계획

def test_non_place_preference_is_planned_and_applied():
    c = Chat([X("recommend", **FULL), X("update", "반지하·옥탑 싫어")], plans=[FLOOR])
    c.say("이대 공대, 70, 통학 1순위 돈 2순위, 15분 18㎡")
    s = c.say("반지하랑 옥탑은 싫어")
    assert s["status"] == "ok" and s["profile_changed"] is True
    conds = [cd for e in s["current_profile"]["exclusions"] for cd in e["conditions"]]
    assert any(cd["field"] == "floor_kind" for cd in conds)
    assert s["response"].startswith("바뀐 것:")
    # 계획 프롬프트에 도구 목록과 지금 조건이 들어간다
    assert "tools" in c.llm.plan_contexts[0]["catalog"] and c.llm.plan_contexts[0]["exclusions"] == []


def test_onsite_only_is_guidance_not_exclusion():
    c = Chat([X("recommend", **FULL), X("smalltalk", "방음 잘 되는 곳")], plans=[PlanDraft(onsite_checks=["방음"])])
    c.say("조건")
    s = c.say("방음 잘 되는 곳이면 좋겠어")
    assert s["intent"] == "update"  # 기호만 말한 턴 = 조건 변경
    assert not s["current_profile"]["exclusions"] and s["profile_changed"] is False
    assert "집 보러 가면" in s["response"] and "방음" in s["response"]


def test_same_target_replaces_with_note():
    bar = lambda v: PlanDraft(conditions=[ConditionDraft(tool="amenity", label="술집", amenity="bar", metric="nearest_m",
                                                         op="<=", value=v)])
    c = Chat([X("recommend", **FULL), X("update", "밤에 조용한 곳"), X("update", "아주 조용해야 해")],
             plans=[bar(30), bar(50)])
    c.say("조건")
    c.say("밤에 조용한 곳")
    s = c.say("아주 조용해야 해")
    # 시설 데이터가 없는 환경이면 amenity 가 장소 검색(nearest_distance '술집')으로 바뀐다 → label 로 센다
    conds = [cd for e in s["current_profile"]["exclusions"] for cd in e["conditions"] if cd["label"].startswith("술집")]
    assert len(conds) == 1 and conds[0]["exclude_if"]["value"] == 50
    assert "새 기준으로 바꿨어요" in s["response"]
    assert "- 아주 조용해야 해: 가장 가까운" in s["response"] or "50m" in s["response"]  # 조건 문장이 비지 않는다


def test_plan_failure_keeps_profile():
    c = Chat([X("recommend", **FULL), X("update", "반지하 싫어")], plans=[LLMError("혼잡", "busy")])
    before = c.say("조건")["current_profile"]["exclusions"]
    s = c.say("반지하 싫어")
    assert s["current_profile"]["exclusions"] == before and any("바꾸지 못했어요" in n for n in s["notes"])


def test_plan_from_draft_validation():
    draft = PlanDraft(conditions=[
        ConditionDraft(tool="listing_field", label="보증금", field="deposit_manwon", op=">", value=500),  # 조건 변경으로
        ConditionDraft(tool="listing_field", label="곰팡이", field="없는칸", op="==", value=1),            # 현장 확인으로
        ConditionDraft(tool="listing_field", label="치안", field="없는칸", op="==", value=1),              # 확인 불가로
    ], unsupported=["오르막"])
    plan, notes = tools.plan_from_draft(draft, "기호")
    assert not plan.conditions and "곰팡이" in plan.onsite_checks
    assert set(plan.unsupported) >= {"오르막", "치안"} and any("보증금 상한" in n for n in notes)


# ---------------------------------------------------------------- 범위 밖 · 금지 문구

def test_out_of_scope_refusal_points_to_contract_check():
    c = Chat([X("recommend", **FULL), X("out_of_scope", topic="fraud_registry")])
    c.say("조건")
    s = c.say("1등 집 계약해도 사기 안 당해?")
    assert s["response"] == REFUSAL["fraud_registry"] and "등기부등본" in s["response"]
    assert s["profile_changed"] is False and s["trace"]["explanation_source"] == "template_refusal"


def test_policy_mode_turns_contract_question_into_checklist():
    c = Chat([X("out_of_scope", topic="contract_legal")], ["점검 목록을 안내해요."])
    s = c.say("전세 계약 특약 이렇게 써도 돼?", mode="policy")
    assert s["intent"] == "policy" and s["policy"]["kind"] == "contract_safety"


def test_policy_mode_keeps_unrelated_refusal():
    c = Chat([X("out_of_scope", topic="unrelated")])
    s = c.say("오늘 날씨 어때?", mode="policy")
    assert s["intent"] == "out_of_scope" and s["response"] == REFUSAL["unrelated"]


def test_timetable_photo_in_chat_is_guided_to_profile():
    c = Chat([X("smalltalk")])
    s = c.say("시간표 캡처 보낼 테니 읽어 줘")
    assert s["response"] == REFUSAL["timetable_photo"] and "시간표 캡처로 불러오기" in s["response"]


def test_forbidden_phrase_falls_back():
    c = Chat([X("recommend", **FULL)], ["1위 집은 사기가 아닙니다. 계약해도 됩니다."])
    s = c.say("추천해 줘")
    assert s["trace"]["explanation_source"] == "fallback_forbidden"
    assert not any(w in s["response"] for w in FORBIDDEN_PHRASES)


# ---------------------------------------------------------------- 규칙 문서

def test_rules_doc_sections_in_prompts():
    assert rules.version() == "1.3"
    assert "R-701" in extract_system() and "R-703" in extract_system()
    assert "R-501" in plan_system() and "R-508" in plan_system()
    assert "out_of_scope" in rules.section(2)  # 이 브랜치 의도 이름
