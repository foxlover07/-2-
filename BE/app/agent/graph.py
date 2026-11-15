"""LangGraph 챗봇 흐름 (통합안 추천 연결, agent-integrated-design.md · chatbot-rules.md).

START → extract        (의도·조건 추출, LLM 1회)
      → update_profile (서버 검증 후 반영 / 가정은 따로 / 되돌리기 / 제외 조건 지우기)
      → plan_exclusion (기호가 있으면: 계획 LLM 1회 → 검증 → 미리보기 → 0개면 적용하지 않음, 아니면 바로 적용, R-505·506)
      → check_required (필수 조건, 모자라면 2개까지 묻기, R-601)
      → recommend | what_if | compare_previous | explain_prep (통합안 엔진, 도구)
      → respond        (바뀐 것 → 추천 요약(LLM 1회, 숫자 검증) → 예산 초과 → 확인할 것 → 광고 매물 안내, R-901)
      → END

- 확인 질문은 없다 (11/15 결정). 되돌리기는 "○○ 조건 빼 줘"와 되돌리기 의도로 한다.
- 인사·범위 밖·되묻기·0개 남음 답은 LLM 없이 코드 문장으로 만든다 (거절 규칙은 하나도 틀리면 안 되므로, R-701).
- 상태 저장: 개발용 InMemorySaver (session_id = thread_id). 서버를 다시 켜면 대화가 사라진다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agent import rules, tools
from app.agent.llm import AgentLLM, LLMError, LLMUnavailable
from app.agent.schemas import AgentProfile, Comparison, RecSummary, TurnExtraction
from app.exclusions import ExclusionPlan, tool_catalog
from app.models import Property
from app.scoring import FACTOR_LABELS, UnknownBuildingError

MAX_MESSAGES = 20
LISTING_NOTE = "광고 매물이라 지금도 계약할 수 있는지는 원문 링크에서 확인해 주세요."
LARGER_NOTE = "넓은 면적은 상관없음으로 두었어요. 넓을수록 좋거나 싫으면 말해 주세요."
LLM_ERROR_TEXT = {
    "busy": "AI 서버가 지금 혼잡해 응답을 받지 못했어요. 잠시 뒤 다시 보내 주세요.",
    "quota": "AI 사용 한도를 넘어 지금은 답할 수 없어요. 한도가 풀린 뒤 다시 시도해 주세요.",
    "auth": "AI 서버 인증에 실패했어요 (서버 설정 확인 필요).",
    "not_found": "설정된 AI 모델을 쓸 수 없어요 (서버 설정 확인 필요).",
    "bad_response": "말씀을 조건으로 읽지 못했어요. 조금 다르게 말해 주세요.",
    "other": "AI 응답을 받지 못했어요. 잠시 뒤 다시 시도해 주세요.",
}
OFFER = "대신 조건에 맞는 매물을 더 찾아 드릴 수 있어요."
REFUSAL = {  # R-701 (판단하지 않고 안내처를 하나 이상)
    "contract_legal": "계약·특약·보증금 반환 같은 법률 문제는 제가 판단할 수 없어요. 대한법률구조공단(국번 없이 132)이나 "
                      "주택임대차분쟁조정위원회에 상담해 보세요. " + OFFER,
    "fraud_registry": "계약해도 안전한지, 사기인지는 제가 판단할 수 없어요. 계약 전에 인터넷등기소에서 등기부등본을 떼어 "
                      "집주인과 권리 관계를 확인하고, 공인중개사 등록 여부도 확인해 보세요. " + OFFER,
    "landlord": "집주인이나 관리 상태는 데이터로 알 수 없어요. 집 보러 갈 때 직접 확인해 보세요. " + OFFER,
    "listing_guarantee": "광고 매물이라 실제로 있는지, 지금 계약할 수 있는지는 보장할 수 없어요. 원문 링크에서 확인해 주세요. " + OFFER,
    "market_forecast": "시세가 오르거나 내릴지는 예측해 드리지 않아요. 대신 지금 조건으로 예산 안 매물을 비교해 드릴 수 있어요.",
    "unsupported_region": "지금은 " + "·".join(["이화여자대학교", "고려대학교", "서강대학교", "서울과학기술대학교"])
                          + " 인근 원룸·오피스텔 월세 매물만 추천할 수 있어요. 이 중 한 곳을 알려 주시면 바로 찾아 드릴게요.",
    "unrelated": "저는 대학가 자취방 추천만 도와드려요. 학교·예산·통학 시간 같은 조건을 말해 주시면 매물을 찾아 드릴게요.",
    "timetable_photo": "시간표 사진은 읽지 않아요. 생활 프로필의 시간표 입력에서 요일·시작·종료·강의동을 직접 넣거나, "
                       "\"월수 9시부터 10시 반까지 ECC\"처럼 말해 주시면 넣어 드릴게요.",
}
GREETING = "안녕하세요! 대학가 자취방 찾는 걸 도와드릴게요."
MORE_CONDITIONS = ("더 원하는 조건이 있으세요? \"밤에 조용한 곳\", \"편의점 가까운 곳\", \"반지하 싫어\"처럼 말해 주시면 "
                   "바로 반영해서 다시 추천해 드릴게요.")
PHOTO_WORDS = ("사진", "이미지", "캡처", "캡쳐", "스크린샷")


class AgentState(TypedDict, total=False):
    # 대화 내내 유지
    session_id: str
    messages: list[dict[str, str]]
    current_profile: dict[str, Any] | None
    previous_profile: dict[str, Any] | None
    last_recommendation: dict[str, Any] | None
    previous_recommendation: dict[str, Any] | None
    last_asks: list[str]
    # 이번 턴
    user_message: str
    fe_profile: dict[str, Any] | None
    intent: str | None
    extraction: dict[str, Any] | None
    notes: list[str]
    asks: list[str]
    scenario_profile: dict[str, Any] | None
    scenario_recommendation: dict[str, Any] | None
    comparison: dict[str, Any] | None
    profile_changed: bool
    changed_lines: list[str]
    exclusion_text: str | None
    exclusion_applied: bool
    exclusion_blocked: bool
    recommended: bool
    template: str | None
    status: str
    llm_error_kind: str | None
    response: str
    explanation: str | None
    facts: dict[str, Any] | None
    trace_steps: list[dict[str, Any]]
    trace: dict[str, Any] | None


def _profile(d: dict[str, Any] | None) -> AgentProfile:
    return AgentProfile.model_validate(d) if d else AgentProfile()


def _dump(m: Any) -> dict[str, Any]:
    return m.model_dump(mode="json")


def profile_context(current: AgentProfile, previous: AgentProfile | None, last_top: list[dict[str, Any]] | None,
                    history: list[dict[str, str]], last_asks: Sequence[str] = ()) -> dict[str, Any]:
    """extract 에 주는 맥락. 큰 결과는 넣지 않는다."""
    cur = current.model_dump(mode="json", exclude_defaults=True, exclude={"exclusions"})
    prev = previous.model_dump(mode="json", exclude_defaults=True, exclude={"exclusions"}) if previous else None
    return {
        "current_profile": cur,
        "previous_profile": prev,
        "exclusions": [c.label for c in tools.all_conditions(current.exclusions)],
        "last_question": list(last_asks),
        "last_top": last_top,
        "recent_messages": history[-6:],
    }


# ---------------------------------------------------------------- 노드

Data = tuple[Sequence[Property], dict[str, dict[str, float]]]


def make_nodes(get_llm: Callable[[], AgentLLM], get_data: Callable[[], Data],
               get_searcher: Callable[[AgentProfile], Any] | None = None,
               trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def searcher_for(p: AgentProfile) -> Any:
        return get_searcher(p) if get_searcher else None

    def extract(state: AgentState) -> AgentState:
        out: AgentState = {
            "intent": None, "extraction": None, "notes": [], "asks": [], "scenario_profile": None,
            "scenario_recommendation": None, "comparison": None, "profile_changed": False, "changed_lines": [],
            "exclusion_text": None, "exclusion_applied": False, "exclusion_blocked": False, "recommended": False,
            "template": None, "status": "ok", "llm_error_kind": None, "response": "", "explanation": None,
            "facts": None, "trace": None,
        }
        current = state.get("current_profile")
        if state.get("fe_profile"):  # FE 에 저장된 생활 프로필을 이번 턴의 현재 조건으로 (폼과 챗봇이 같은 조건)
            fe = _profile(state["fe_profile"])
            if current is None or fe != _profile(current):
                current = _dump(fe)
        out["current_profile"] = current
        last = state.get("last_recommendation")
        last_top = [{"rank": t["rank"], "property_id": t["property_id"], "address": t.get("address")}
                    for t in (last or {}).get("top", [])] or None
        ctx = profile_context(_profile(current), _profile(state["previous_profile"]) if state.get("previous_profile") else None,
                              last_top, state.get("messages", []), state.get("last_asks", []))
        try:
            llm = get_llm()
            ex = llm.extract(state["user_message"], ctx)
            out["trace"] = {"extract_model": getattr(llm, "last_model", None)}
        except LLMUnavailable as e:
            out.update(status="llm_unavailable", notes=[str(e)])
            return out
        except LLMError as e:
            out.update(status="llm_error", notes=[str(e)], llm_error_kind=getattr(e, "kind", "other"))
            return out
        # 시간표 사진은 읽지 않는다 (R-202): 말에 사진이 나오면 의도와 상관없이 안내
        if any(w in state["user_message"] for w in PHOTO_WORDS) and "시간표" in state["user_message"]:
            ex = ex.model_copy(update={"intent": "out_of_scope", "scope_topic": "timetable_photo"})
        out.update(intent=ex.intent, extraction=_dump(ex))
        return out

    def update_profile(state: AgentState) -> AgentState:
        if state["status"] != "ok":
            return {}
        ex = TurnExtraction.model_validate(state["extraction"])
        current = _profile(state.get("current_profile"))
        notes = list(state.get("notes", []))
        out: AgentState = {}
        intent = ex.intent
        if ex.changes.university and tools.normalize_university(ex.changes.university) is None \
                and intent not in ("out_of_scope", "greeting"):
            # 지원하지 않는 학교를 말하면 학교를 바꾸지 않고 범위를 안내한다 (R-101). 매물을 지어내지 않는다.
            ex = ex.model_copy(update={"intent": "out_of_scope", "scope_topic": "unsupported_region"})
            out.update(intent="out_of_scope", extraction=_dump(ex))
            return out
        if intent == "greeting":  # 인사 + 필수 조건이 비어 있으면 묻는다 (다음 턴의 짧은 답을 이 질문으로 채운다, R-604)
            keys = tools.questions(current)
            out.update(asks=keys, last_asks=keys)
        if ex.unsupported:
            notes.append(f"{', '.join(ex.unsupported)}{tools.josa(ex.unsupported[-1], '은', '는')} 지금 데이터로 반영할 수 없어요 "
                         "(자주 가는 곳까지 거리는 데이터 준비 중이에요).")

        def commit(new: AgentProfile, lines: list[str]) -> None:
            out.update(previous_profile=_dump(current), previous_recommendation=state.get("last_recommendation"),
                       current_profile=_dump(new), profile_changed=True, changed_lines=lines)

        if intent in ("update", "recommend", "exclude"):
            new, n, asks = tools.apply_changes(current, ex.changes)
            notes += n
            out["asks"] = asks
            if new != current:
                commit(new, tools.change_lines(current, new))
        elif intent == "what_if":
            scen, n, asks = tools.apply_changes(current, ex.changes)
            notes += n
            out["asks"] = asks
            if scen == current:
                notes.append("가정으로 바뀌는 조건이 없어 지금 조건과 같아요.")
            out["scenario_profile"] = _dump(scen)
        elif intent == "revert":
            prev = state.get("previous_profile")
            if not prev:
                notes.append("되돌릴 이전 조건이 없어요.")
            else:
                lines = tools.change_lines(current, _profile(prev))
                out.update(current_profile=prev, previous_profile=_dump(current),
                           last_recommendation=state.get("previous_recommendation"),
                           previous_recommendation=state.get("last_recommendation"), profile_changed=True,
                           changed_lines=lines or ["이전 조건으로 되돌렸어요."])
        elif intent == "remove_exclusion":
            kept, removed = tools.remove_exclusions(current.exclusions, ex.remove_exclusion)
            if removed:
                new = current.model_copy(update={"exclusions": kept})
                commit(new, [f"제외 조건 지움: {', '.join(removed)}"])
            else:
                have = [c.label for c in tools.all_conditions(current.exclusions)]
                notes.append(f"지울 제외 조건을 찾지 못했어요. 지금 제외 조건: {', '.join(have) if have else '없음'}.")
        out["notes"] = notes
        return out

    def plan_exclusion(state: AgentState) -> AgentState:
        ex = TurnExtraction.model_validate(state["extraction"])
        request = (ex.exclusion_request or (state["user_message"] if state.get("intent") == "exclude" else "")).strip()
        if not request:
            return {}
        current = _profile(state.get("current_profile"))
        props, travel = get_data()
        uni_props = [p for p in props if p.university == current.university] if current.university else list(props)
        ctx = {"catalog": tool_catalog(uni_props or None),
               "exclusions": [c.describe() for c in tools.all_conditions(current.exclusions)]}
        notes = list(state.get("notes", []))
        try:
            draft = get_llm().plan(request, ctx)
        except (LLMError, LLMUnavailable):
            notes.append("기호를 확인 조건으로 바꾸지 못했어요. 잠시 뒤 다시 말해 주세요.")
            return {"notes": notes}
        plan, n = tools.plan_from_draft(draft, request, uni_props or None)
        notes += n
        if not plan.conditions:
            return {"notes": notes, "exclusion_text": plan.applied_text(None)}
        merged, replaced = tools.merge_plans(current.exclusions, plan)
        others = [x for x in merged if x is not merged[-1]]
        prev = tools.preview(current, merged[-1], props, travel, existing=others) if current.university else None
        if prev is not None and prev.before > 0 and prev.remaining == 0:  # R-506: 적용하지 않고 묻는다
            return {"notes": notes, "exclusion_text": plan.blocked_text(prev), "exclusion_blocked": True}
        text = merged[-1].model_copy(update={"unsupported": plan.unsupported, "onsite_checks": plan.onsite_checks}
                                     ).applied_text(prev)
        if replaced:
            text += f"\n(기존 조건 {', '.join(replaced)}{tools.josa(replaced[-1], '은', '는')} 새 기준으로 바꿨어요.)"
        new = current.model_copy(update={"exclusions": merged})
        out: AgentState = {"notes": notes, "exclusion_text": text, "exclusion_applied": True,
                           "current_profile": _dump(new), "profile_changed": True}
        if not state.get("profile_changed"):  # 이 턴에서 처음 바뀌면 이전 조건을 남긴다 (되돌리기용)
            out.update(previous_profile=_dump(current), previous_recommendation=state.get("last_recommendation"))
        return out

    def check_required(state: AgentState) -> AgentState:
        target = _profile(state.get("scenario_profile") or state.get("current_profile"))
        keys = tools.questions(target, state.get("asks", []))
        if keys:
            return {"asks": keys, "status": "need_info", "last_asks": keys}
        return {"asks": [], "last_asks": []}

    def _run(p: AgentProfile) -> RecSummary:
        props, travel = get_data()
        return tools.run_recommendation(p, props, travel, searcher_for(p))

    def recommend(state: AgentState) -> AgentState:
        current = _profile(state["current_profile"])
        try:
            rec = _run(current)
        except UnknownBuildingError as e:
            return {"status": "blocked", "notes": [*state.get("notes", []), f"추천할 수 없어요: {e}"]}
        out: AgentState = {"last_recommendation": _dump(rec), "recommended": True}
        prev = state.get("previous_recommendation")
        if state.get("profile_changed") and prev and state.get("previous_profile"):
            out["comparison"] = _dump(tools.compare(RecSummary.model_validate(prev), rec, _profile(state["previous_profile"]),
                                                    current, "이전 조건", "지금 조건"))
        return out

    def what_if(state: AgentState) -> AgentState:
        current = _profile(state["current_profile"])
        scen = _profile(state["scenario_profile"])
        out: AgentState = {}
        try:
            base = None if tools.missing_fields(current) else _run(current)
            scen_rec = _run(scen)
        except UnknownBuildingError as e:
            return {"status": "blocked", "notes": [*state.get("notes", []), f"추천할 수 없어요: {e}"]}
        if base is not None:
            out["last_recommendation"] = _dump(base)
            out["comparison"] = _dump(tools.compare(base, scen_rec, current, scen, "지금 조건", "가정한 조건"))
        out.update(scenario_recommendation=_dump(scen_rec), recommended=True)
        return out

    def compare_previous(state: AgentState) -> AgentState:
        last, prev = state.get("last_recommendation"), state.get("previous_recommendation")
        if not last or not prev:
            return {"notes": [*state.get("notes", []), "비교할 이전 추천 결과가 없어요. 조건을 바꿔 다시 추천받으면 비교할 수 있어요."]}
        cmp = tools.compare(RecSummary.model_validate(prev), RecSummary.model_validate(last),
                            _profile(state.get("previous_profile")), _profile(state.get("current_profile")),
                            "이전 조건", "지금 조건")
        return {"comparison": _dump(cmp)}

    def explain_prep(state: AgentState) -> AgentState:
        """설명할 결과가 없고 조건이 다 있으면 먼저 추천한다 (LLM 호출 없음)."""
        current = _profile(state.get("current_profile"))
        if state.get("last_recommendation") or tools.missing_fields(current):
            return {}
        try:
            return {"last_recommendation": _dump(_run(current))}
        except UnknownBuildingError:
            return {}

    def respond(state: AgentState) -> AgentState:
        text, explanation, source, facts, explain_model = compose(state, get_llm)
        msgs = [*state.get("messages", []), {"role": "user", "content": state["user_message"]},
                {"role": "assistant", "content": text}][-MAX_MESSAGES:]
        trace = make_trace(state, source, explain_model)
        if trace_sink is not None:
            try:
                trace_sink(trace)
            except Exception:  # noqa: BLE001 — 기록 실패가 대화를 막지 않게
                pass
        return {"response": text, "explanation": explanation, "facts": facts, "messages": msgs, "trace": trace}

    return {"extract": extract, "update_profile": update_profile, "plan_exclusion": plan_exclusion,
            "check_required": check_required, "recommend": recommend, "what_if": what_if,
            "compare_previous": compare_previous, "explain_prep": explain_prep, "respond": respond}


# ---------------------------------------------------------------- 분기

def route_after_update(state: AgentState) -> str:
    if state.get("status") != "ok":
        return "respond"
    intent = state.get("intent")
    if intent in ("greeting", "out_of_scope"):
        return "respond"
    ex = state.get("extraction") or {}
    if intent in ("update", "recommend", "exclude") and (ex.get("exclusion_request") or intent == "exclude"):
        return "plan_exclusion"
    return route_main(state)


def route_main(state: AgentState) -> str:
    intent = state.get("intent")
    if state.get("exclusion_blocked"):
        return "respond"
    if intent == "exclude" and not state.get("exclusion_applied") and not state.get("profile_changed"):
        return "respond"  # 확인할 수 없는 기호만 말함: 바뀐 것 없음 + 현장 확인 안내
    if intent == "compare":
        return "compare_previous"
    if intent == "explain":
        return "explain_prep"
    if intent == "revert" and not state.get("profile_changed"):
        return "respond"
    if intent == "remove_exclusion" and not state.get("profile_changed"):
        return "respond"
    return "check_required"


def route_after_required(state: AgentState) -> str:
    if state.get("status") != "ok":
        return "respond"
    return "what_if" if state.get("intent") == "what_if" else "recommend"


def _timed(name: str, fn: Callable[[AgentState], AgentState]) -> Callable[[AgentState], AgentState]:
    def run(state: AgentState) -> AgentState:
        t = time.perf_counter()
        out = fn(state) or {}
        prev = [] if name == "extract" else list(state.get("trace_steps", []))
        return {**out, "trace_steps": [*prev, {"node": name, "ms": round((time.perf_counter() - t) * 1000)}]}
    return run


def build_graph(get_llm: Callable[[], AgentLLM], get_data: Callable[[], Data],
                get_searcher: Callable[[AgentProfile], Any] | None = None, checkpointer=None,
                trace_sink: Callable[[dict[str, Any]], None] | None = None):
    nodes = make_nodes(get_llm, get_data, get_searcher, trace_sink)
    g = StateGraph(AgentState)
    for name, fn in nodes.items():
        g.add_node(name, fn if name == "respond" else _timed(name, fn))
    g.add_edge(START, "extract")
    g.add_edge("extract", "update_profile")
    main = ["respond", "plan_exclusion", "compare_previous", "explain_prep", "check_required"]
    g.add_conditional_edges("update_profile", route_after_update, main)
    g.add_conditional_edges("plan_exclusion", route_main, [m for m in main if m != "plan_exclusion"])
    g.add_conditional_edges("check_required", route_after_required, ["respond", "what_if", "recommend"])
    for n in ("recommend", "what_if", "compare_previous", "explain_prep"):
        g.add_edge(n, "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


def run_turn(graph: Any, session_id: str, message: str, fe_profile: dict[str, Any] | None) -> AgentState:
    cfg = {"configurable": {"thread_id": session_id}}
    return graph.invoke({"user_message": message, "session_id": session_id, "fe_profile": fe_profile}, config=cfg)


# ---------------------------------------------------------------- 응답 (R-901 순서)

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _nums(text: str) -> set[float]:
    return {float(x) for x in _NUM.findall(text.replace(",", ""))}


def numbers_grounded(text: str, facts: dict[str, Any]) -> bool:
    """답에 나온 숫자가 모두 사실 JSON 에 있는지 (R-801). 순서 표현(0~10)은 허용한다."""
    allowed = _nums(json.dumps(facts, ensure_ascii=False)) | {float(i) for i in range(0, 11)}
    return _nums(text) <= allowed


def _rec_facts(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    return {
        "university": rec["university"],
        "priorities": [FACTOR_LABELS.get(x, x) for x in rec["priorities"]],
        "weights_percent": {FACTOR_LABELS.get(k, k): round(v * 100) for k, v in rec["weights"].items()},
        "candidates": rec["total_ranked"],
        "excluded": rec["excluded_count"],
        "excluded_by_condition": rec["exclusion_counts"],
        "top": [{"rank": t["rank"], "address": t.get("address"), "type": t.get("property_type"),
                 "deposit_manwon": t["deposit_manwon"], "vs_target": t["vs_target"], "over_budget": t["over_budget"],
                 "score": t["score"]} for t in rec["top"]],
        "not_recommended": rec.get("not_recommended", []),
    }


def _cmp_facts(cmp: dict[str, Any] | None, recs: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    if not cmp:
        return None
    addr = {t["property_id"]: t.get("address") for r in recs if r for t in r["top"]}
    return {
        "before": cmp["label_before"], "after": cmp["label_after"],
        "candidates_before": cmp["total_before"], "candidates_after": cmp["total_after"],
        "top_changes": [{"address": addr.get(c["property_id"], c["property_id"]), "rank_before": c["rank_before"],
                         "rank_after": c["rank_after"]} for c in cmp["score_changes"]],
    }


def how_it_works() -> str:
    """추천 방식 명세 (설명용). 규칙 문서 4장."""
    return rules.section(4)


def _shown_rec(state: AgentState) -> dict[str, Any] | None:
    if state.get("scenario_recommendation"):
        return state["scenario_recommendation"]
    if state.get("recommended") or state.get("intent") == "explain":
        return state.get("last_recommendation")
    return None


def changed_block(state: AgentState) -> str:
    lines = list(state.get("changed_lines") or [])
    ex_text = state.get("exclusion_text")
    if ex_text and not state.get("exclusion_blocked"):
        body = ex_text[len("바뀐 것: "):] if ex_text.startswith("바뀐 것: ") else ex_text
        if not lines:
            return ex_text
        return "바뀐 것:\n" + "\n".join(f"- {x}" for x in lines) + "\n- " + body
    if lines:
        return "바뀐 것:\n" + "\n".join(f"- {x}" for x in lines)
    return ""


def tail_lines(state: AgentState, rec: dict[str, Any] | None) -> list[str]:
    """코드가 붙이는 끝부분: 예산 초과 → 확인할 것·안내 → 넓은 면적 안내 → 광고 매물 안내 (R-901·303·102)."""
    out: list[str] = []
    profile = _profile(state.get("scenario_profile") or state.get("current_profile"))
    if rec:
        for t in rec["top"]:
            if t["over_budget"]:
                out.append(f"예산 초과: {t['rank']}위 {t.get('address') or t['property_id']} — {t['vs_target'][0]}")
        skip = ("자주 가는 곳은 데이터 준비 중", "시간표를 입력하지 않아")
        out += [n for n in rec["notes"] if not n.startswith(skip)]
    out += [n for n in state.get("notes", []) if n not in out]
    if rec:
        if profile.larger_area is None:
            out.append(LARGER_NOTE)
        out.append(LISTING_NOTE)
    return out


def fallback_summary(rec: dict[str, Any] | None, cmp: dict[str, Any] | None) -> str:
    """LLM 없이 사실만으로 만든 추천 요약 (LLM 실패·숫자 검증 실패)."""
    lines: list[str] = []
    if rec:
        lines.append(f"{rec['university']} 기준, 조건에 맞는 매물 {rec['total_ranked']}개 중 상위 매물이에요.")
        for t in rec["top"]:
            dep = "보증금 정보 없음" if t["deposit_manwon"] is None else f"보증금 {t['deposit_manwon']:g}만 원"
            lines.append(f"{t['rank']}위 {t.get('address') or t['property_id']} ({t.get('property_type') or '-'}) · {dep} · "
                         + " · ".join(t["vs_target"]))
        if not rec["top"]:
            lines.append("조건에 맞는 매물이 없어요. 예산이나 제외 조건을 풀어 보세요.")
    if cmp:
        lines.append(f"{cmp['label_before']} {cmp['total_before']}개 → {cmp['label_after']} {cmp['total_after']}개.")
    return "\n".join(lines)


def compose(state: AgentState, get_llm: Callable[[], AgentLLM]) -> tuple[str, str | None, str, dict[str, Any], str | None]:
    """→ (답 전체, LLM 설명 부분, 출처, 사실, 설명 모델)."""
    status = state.get("status", "ok")
    intent = state.get("intent")
    profile = _profile(state.get("current_profile"))
    ex = state.get("extraction") or {}
    facts: dict[str, Any] = {"mode": intent, "status": status}

    def done(parts: list[str], source: str, explanation: str | None = None, model: str | None = None):
        text = "\n\n".join(p for p in parts if p)
        # 코드가 만든 문장(바뀐 것·끝부분·안내)의 숫자도 근거로 둔다. LLM 설명은 넣지 않는다 (넣으면 숫자 검증이 늘 통과)
        facts["code_text"] = "\n\n".join(p for p in parts if p and p != explanation)
        return text, explanation, source, facts, model

    if status in ("llm_unavailable", "llm_error"):
        msg = ("AI 대화 기능이 설정되지 않았어요. 생활 프로필 화면에서 조건을 넣으면 추천은 그대로 받을 수 있어요."
               if status == "llm_unavailable" else
               LLM_ERROR_TEXT.get(state.get("llm_error_kind") or "other", LLM_ERROR_TEXT["other"])
               + " 생활 프로필 화면의 추천은 그대로 쓸 수 있어요.")
        return done([msg], "fallback_error")
    if intent == "greeting":
        keys = state.get("asks") or []
        ask = tools.question_text(profile, keys) if keys else MORE_CONDITIONS
        return done([GREETING, ("추천하려면 " + ask) if keys else ask], "template_greeting")
    if intent == "out_of_scope":
        return done([REFUSAL.get(ex.get("scope_topic") or "unrelated", REFUSAL["unrelated"])], "template_refusal")

    block = changed_block(state)
    notes = state.get("notes", [])
    if state.get("exclusion_blocked"):
        return done([block, state.get("exclusion_text") or ""], "template_blocked")
    if status == "need_info":
        target = _profile(state.get("scenario_profile") or state.get("current_profile"))
        ask = tools.question_text(target, state.get("asks", []))
        return done([block, "\n".join(notes), ("추천하려면 " + ask) if ask else ""], "template_question")
    if status == "blocked":
        return done([block, "\n".join(notes)], "template_blocked")

    rec = _shown_rec(state)
    cmp = state.get("comparison")
    facts.update({
        "changed": block or None,
        "result": _rec_facts(rec),
        "comparison": _cmp_facts(cmp, [state.get("last_recommendation"), state.get("previous_recommendation"),
                                       state.get("scenario_recommendation")]),
        "asked_rank": ex.get("property_rank"),
        "notes": notes,
        "how_it_works": how_it_works() if intent == "explain" else None,
    })
    if intent == "what_if":
        facts["scenario_conditions"] = tools.change_lines(profile, _profile(state.get("scenario_profile")))
    needs_llm = rec is not None or cmp is not None or intent == "explain"
    explanation, source, model = None, "template", None
    if needs_llm:
        try:
            llm = get_llm()
            explanation = (llm.explain(state["user_message"], facts) or "").strip()
            model = getattr(llm, "last_model", None)
            source = "llm"
            if not explanation or not numbers_grounded(explanation, facts):
                explanation, source = fallback_summary(rec, cmp), "fallback_ungrounded"
        except (LLMError, LLMUnavailable):
            explanation, source = fallback_summary(rec, cmp), "fallback_llm_failed"
    tail = tail_lines(state, rec)
    if not needs_llm and not block and not tail:
        tail = [MORE_CONDITIONS]
    return done([block, explanation or "", "\n".join(tail)], source, explanation, model)


def make_trace(state: AgentState, explanation_source: str, explain_model: str | None) -> dict[str, Any]:
    steps = list(state.get("trace_steps", []))
    ex = state.get("extraction") or {}
    rec = _shown_rec(state)
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "session_id": state.get("session_id"),
        "message": state.get("user_message"),
        "intent": state.get("intent"),
        "changes": {k: v for k, v in (ex.get("changes") or {}).items() if v not in (None, False)},
        "exclusion_request": ex.get("exclusion_request"),
        "status": state.get("status"),
        "llm_error_kind": state.get("llm_error_kind"),
        "nodes": steps,
        "ms_total": sum(s["ms"] for s in steps),
        "extract_model": (state.get("trace") or {}).get("extract_model"),
        "explain_model": explain_model,
        "explanation_source": explanation_source,
        "asks": state.get("asks", []),
        "profile_changed": bool(state.get("profile_changed")),
        "exclusion_applied": bool(state.get("exclusion_applied")),
        "exclusion_blocked": bool(state.get("exclusion_blocked")),
        "top": [t["property_id"] for t in rec["top"]] if rec else [],
    }


def to_response_parts(state: AgentState) -> dict[str, Any]:
    rec = _shown_rec(state)
    cmp = state.get("comparison")
    return {
        "assistant_message": state.get("response", ""),
        "updated_profile": _profile(state["current_profile"]) if state.get("current_profile") else None,
        "profile_changed": bool(state.get("profile_changed")),
        "recommendation_results": RecSummary.model_validate(rec) if rec else None,
        "comparison_results": Comparison.model_validate(cmp) if cmp else None,
        "missing_fields": state.get("asks", []),
        "intent": state.get("intent"),
        "status": state.get("status", "ok"),
        "trace": state.get("trace"),
    }


__all__ = ["build_graph", "run_turn", "to_response_parts", "numbers_grounded", "profile_context", "ExclusionPlan"]
