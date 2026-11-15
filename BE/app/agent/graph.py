"""LangGraph 단일 에이전트 흐름.

START → extract (의도·조건 추출, LLM)
      → update_profile (서버 검증 후 반영 / 가정은 따로 / 되돌리기)
      → (설명·잡담·비교면 바로 해당 노드)
      → check_required (필수 조건 확인, 모자라면 질문)
      → recommend | what_if | analyze | compare (통합안 엔진·분석 도구)
      → validate (결과 검증)
      → respond (설명 생성 + 숫자 검증, 실패하면 사실만 담은 문장)
      → END

상태 저장: 개발용 InMemorySaver (session_id = thread_id). 서버를 다시 켜면 대화가 사라진다.
배포에서 대화를 유지하려면 영속 저장(예: Postgres/SQLite checkpointer)이 필요하다 — 후속 과제.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent import tools
from app.agent.llm import AgentLLM, LLMError, LLMUnavailable, profile_context
from app.agent.schemas import AgentProfile, AnalysisOut, Comparison, RecSummary, TurnExtraction
from app.agent.spec import capability_spec
from app.config import _read
from app.data import load_properties
from app.scoring import FACTOR_LABELS

MAX_MESSAGES = 20
LLM_ERROR_TEXT = {
    "busy": "AI 서버가 지금 혼잡해 응답을 받지 못했습니다. 잠시 뒤 다시 보내 주세요.",
    "quota": "AI 사용 한도를 넘어 지금은 답할 수 없습니다. 한도가 풀린 뒤 다시 시도해 주세요.",
    "auth": "AI 서버 인증에 실패했습니다 (서버의 LLM 키 확인 필요).",
    "not_found": "설정된 AI 모델을 쓸 수 없습니다 (서버의 LLM 모델 설정 확인 필요).",
    "bad_response": "AI 응답 형식이 올바르지 않아 조건을 읽지 못했습니다. 조금 다르게 말해 주세요.",
    "other": "AI 응답을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
}
LISTING_NOTE = "광고 매물이라 실제 입주 가능 여부는 원본 링크에서 확인해야 합니다."


class AgentState(TypedDict, total=False):
    # 대화 내내 유지
    session_id: str
    messages: list[dict[str, str]]
    current_profile: dict[str, Any] | None
    previous_profile: dict[str, Any] | None
    last_recommendation: dict[str, Any] | None
    previous_recommendation: dict[str, Any] | None
    # 이번 턴
    user_message: str
    fe_profile: dict[str, Any] | None
    intent: str | None
    extraction: dict[str, Any] | None
    notes: list[str]
    pending_required_fields: list[str]
    scenario_profile: dict[str, Any] | None
    scenario_recommendation: dict[str, Any] | None
    comparison: dict[str, Any] | None
    analysis: dict[str, Any] | None        # FE 용 분석 결과 (AnalysisOut)
    analysis_facts: dict[str, Any] | None  # 설명용 짧은 사실
    policy: dict[str, Any] | None          # 정책 안내 결과 (app.policy.PolicyResult)
    profile_changed: bool
    blocked: str | None
    status: str
    llm_error_kind: str | None  # busy · quota · auth · not_found · bad_response · other
    response: str
    # 턴 기록 (trace): 거친 노드와 걸린 시간, 사용 모델, 설명 출처
    trace_steps: list[dict[str, Any]]
    trace: dict[str, Any] | None
    # 확인 단계: 기존 입력이 지워지는 변경(학교 변경, 전체 되돌리기)은 바로 저장하지 않고 묻는다
    pending_commit: dict[str, Any] | None
    confirm_question: str | None
    confirm_declined: bool


def explain_mode() -> str:
    """AGENT_LLM_EXPLAIN: on(기본, 조건 추출과 같은 LLM) | off(사실 문장만) | gemini · exaone(설명만 그 공급자).
    EXAONE 설명은 60초를 넘겨(2026-11-15 실측) 설명만 빠른 Gemini 로 돌릴 수 있게 했다."""
    v = (_read("AGENT_LLM_EXPLAIN") or "on").strip().lower()
    return "off" if v in ("off", "0", "false", "no") else v


def llm_explain_enabled() -> bool:
    return explain_mode() != "off"


_EXPLAINERS: dict[str, Any] = {}


def explain_llm(get_llm: Callable[[], AgentLLM]) -> AgentLLM:
    """설명에 쓸 LLM. on 이면 조건 추출과 같은 것, 공급자 이름이면 그 공급자만 (대체 없이, 실패하면 사실 문장)."""
    mode = explain_mode()
    if mode == "on":
        return get_llm()
    if mode not in _EXPLAINERS:
        from app.agent.llm import _FACTORIES
        if mode not in _FACTORIES:
            raise LLMUnavailable(f"알 수 없는 AGENT_LLM_EXPLAIN: {mode!r}")
        _EXPLAINERS[mode] = _FACTORIES[mode]()
    return _EXPLAINERS[mode]


def _profile(d: dict[str, Any] | None) -> AgentProfile:
    return AgentProfile.model_validate(d) if d else AgentProfile()


def _dump(m: Any) -> dict[str, Any]:
    return m.model_dump(mode="json")


# ---------------------------------------------------------------- 노드

def make_nodes(get_llm: Callable[[], AgentLLM], trace_sink: Callable[[dict[str, Any]], None] | None = None):
    def extract(state: AgentState) -> AgentState:
        out: AgentState = {
            "intent": None, "extraction": None, "notes": [], "pending_required_fields": [],
            "scenario_profile": None, "scenario_recommendation": None, "comparison": None,
            "analysis": None, "analysis_facts": None, "policy": None, "profile_changed": False, "blocked": None, "status": "ok", "llm_error_kind": None, "response": "", "trace": None,
            "pending_commit": None, "confirm_question": None, "confirm_declined": False,
        }
        # FE 에 저장된 프로필이 오면 이번 턴의 현재 조건으로 쓴다 (폼과 챗봇이 같은 조건)
        current = state.get("current_profile")
        if state.get("fe_profile"):
            fe = _profile(state["fe_profile"])
            if current is None or fe != _profile(current):
                current = _dump(fe)
        out["current_profile"] = current
        last = state.get("last_recommendation")
        last_top = [
            {"rank": t["rank"], "property_id": t["property_id"], "address": t.get("address")}
            for t in (last or {}).get("top", [])
        ] or None
        ctx = profile_context(_profile(current), _profile(state["previous_profile"]) if state.get("previous_profile") else None,
                              last_top, state.get("messages", []))
        ctx["service_spec"] = capability_spec(_profile(current).university)
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
        out.update(intent=ex.intent, extraction=_dump(ex))
        return out

    def update_profile(state: AgentState) -> AgentState:
        if state["status"] != "ok":
            return {}
        ex = TurnExtraction.model_validate(state["extraction"])
        current = _profile(state.get("current_profile"))
        notes = list(state.get("notes", []))
        out: AgentState = {}
        if ex.unsupported:
            notes.append(f"지금 계산할 수 없는 요청: {', '.join(ex.unsupported)} (데이터 준비 중이라 점수에 반영하지 않습니다).")
        if ex.intent in ("recommend", "update", "analyze"):  # 분석 요청에 같이 온 조건도 저장
            new, n = tools.apply_changes(current, ex.changes)
            notes += n
            if new != current:
                commit = dict(previous_profile=_dump(current), previous_recommendation=state.get("last_recommendation"),
                              current_profile=_dump(new), profile_changed=True)
                q = confirm_question(current, new, "update")
                if q:
                    out.update(pending_commit=commit, confirm_question=q)
                else:
                    out.update(commit)
        elif ex.intent == "what_if":
            scen, n = tools.apply_changes(current, ex.changes)
            notes += n
            if scen == current:
                notes.append("가정으로 바뀌는 조건이 없어 지금 조건과 같습니다.")
            out["scenario_profile"] = _dump(scen)
        elif ex.intent == "revert":
            if not state.get("previous_profile"):
                notes.append("되돌릴 이전 조건이 없습니다.")
            else:
                commit = dict(current_profile=state["previous_profile"], previous_profile=_dump(current),
                              last_recommendation=state.get("previous_recommendation"),
                              previous_recommendation=state.get("last_recommendation"), profile_changed=True)
                q = confirm_question(current, _profile(state["previous_profile"]), "revert")
                if q:
                    out.update(pending_commit=commit, confirm_question=q)
                else:
                    out.update(commit)
        out["notes"] = notes
        return out

    def confirm(state: AgentState) -> AgentState:
        """사용자 답을 기다린다 (LangGraph interrupt). 다시 이어질 때 resume 값(True/False)을 받는다."""
        pend = state.get("pending_commit")
        if not pend:
            return {}
        diff = tools.profile_diff(_profile(state.get("current_profile")), _profile(pend["current_profile"]))
        ok = interrupt({"question": state.get("confirm_question"), "diff": diff})
        if ok is True:
            return {**pend, "pending_commit": None}
        return {"pending_commit": None, "confirm_declined": True,
                "notes": [*state.get("notes", []), "변경하지 않고 지금 조건을 그대로 둡니다."]}

    def check_required(state: AgentState) -> AgentState:
        target = _profile(state.get("scenario_profile") or state.get("current_profile"))
        missing = tools.missing_fields(target)
        out: AgentState = {"pending_required_fields": missing}
        if not missing and tools.university_listing_count(target.university) == 0:
            out["blocked"] = f"{target.university} 매물 데이터가 아직 없어 추천할 수 없습니다."
        if missing:
            out["status"] = "need_info"
        return out

    def recommend(state: AgentState) -> AgentState:
        current = _profile(state["current_profile"])
        rec = tools.run_recommendation(current)
        out: AgentState = {"last_recommendation": _dump(rec)}
        prev = state.get("previous_recommendation")
        if state.get("profile_changed") and prev and state.get("previous_profile"):
            cmp = tools.compare(RecSummary.model_validate(prev), rec, _profile(state["previous_profile"]), current,
                                "이전 조건", "지금 조건")
            out["comparison"] = _dump(cmp)
        return out

    def what_if(state: AgentState) -> AgentState:
        current = _profile(state["current_profile"])
        scen = _profile(state["scenario_profile"])
        out: AgentState = {}
        if tools.missing_fields(current):
            base = None
        else:
            last = state.get("last_recommendation")
            same = last and last.get("request") == tools.to_request(current).model_dump(mode="json")
            base = RecSummary.model_validate(last) if same else tools.run_recommendation(current)
            if not same:
                out["last_recommendation"] = _dump(base)
        scen_rec = tools.run_recommendation(scen)
        out["scenario_recommendation"] = _dump(scen_rec)
        if base is not None:
            out["comparison"] = _dump(tools.compare(base, scen_rec, current, scen, "지금 조건", "가정한 조건"))
        return out

    def analyze(state: AgentState) -> AgentState:
        """참고 분석 (Pareto · Trade-off · 민감도 · 조건 완화 · 실거래 · 편의시설). 추천 순위는 바꾸지 않는다."""
        ex = TurnExtraction.model_validate(state["extraction"])
        if ex.analysis is None:
            return {"notes": [*state.get("notes", []), "어떤 분석을 원하시는지 알 수 없습니다. 예: '돈 더 내면 얼마나 가까워져?', "
                                                       "'1위 월세 주변 시세랑 비교해 줘', '근처 편의점 있어?'"]}
        current = _profile(state["current_profile"])
        out: AgentState = {}
        last = state.get("last_recommendation")
        same = last and last.get("request") == tools.to_request(current).model_dump(mode="json")
        rec = RecSummary.model_validate(last) if same else tools.run_recommendation(current)
        if not same:
            out["last_recommendation"] = _dump(rec)
        res, facts = tools.run_analysis(ex.analysis, current, rec, ex.property_rank)
        out.update(analysis=_dump(res), analysis_facts={"name": tools.ANALYSIS_LABELS[ex.analysis], **facts})
        return out

    def policy(state: AgentState) -> AgentState:
        """주거 지원 정책 안내 (공식 데이터 검색만, 자격 판정 없음). 프로필 필수 조건을 묻지 않는다."""
        from app.policy import search_policies

        ex = TurnExtraction.model_validate(state["extraction"])
        res = search_policies(ex.policy_query or state["user_message"])
        return {"policy": res.model_dump(mode="json")}

    def compare_previous(state: AgentState) -> AgentState:
        last, prev = state.get("last_recommendation"), state.get("previous_recommendation")
        if not last or not prev:
            return {"notes": [*state.get("notes", []), "비교할 이전 추천 결과가 없습니다. 조건을 바꿔 다시 추천받으면 비교할 수 있어요."]}
        cmp = tools.compare(RecSummary.model_validate(prev), RecSummary.model_validate(last),
                            _profile(state.get("previous_profile")), _profile(state.get("current_profile")),
                            "이전 조건", "지금 조건")
        return {"comparison": _dump(cmp)}

    def validate(state: AgentState) -> AgentState:
        """도구 결과 검증: TOP 매물이 실제 데이터에 있고, 순위가 엔진 결과와 맞는지."""
        ids = {p.property_id for p in load_properties()}
        for key in ("last_recommendation", "scenario_recommendation"):
            rec = state.get(key)
            if not rec:
                continue
            top = rec["top"]
            ranks = [t["rank"] for t in top]
            # TOP 은 같은 집 광고를 묶어 엔진 순위를 건너뛸 수 있다 → 1위부터, 오름차순, 엔진 순위와 일치
            if any(t["property_id"] not in ids for t in top) or (ranks and ranks[0] != 1) \
                    or ranks != sorted(set(ranks)) \
                    or any(rec["ranks"].get(t["property_id"], [None])[0] != t["rank"] for t in top):
                return {"status": "error", "notes": [*state.get("notes", []), "추천 결과 검증에 실패했습니다."]}
        return {}

    def respond(state: AgentState) -> AgentState:
        facts = build_facts(state)
        status = state.get("status", "ok")
        explain_model = None
        if status in ("llm_unavailable", "llm_error", "error"):
            text, source = fallback_text(facts), "fallback_error"
        elif state.get("blocked") or state.get("pending_required_fields"):
            text, source = fallback_text(facts), "template_question"
        elif not llm_explain_enabled():
            text, source = fallback_text(facts), "template"
        else:
            try:
                llm = explain_llm(get_llm)
                text = llm.explain(state["user_message"], facts)
                explain_model = getattr(llm, "last_model", None)
                source = "llm"
                if not text or not numbers_grounded(text, facts):
                    text, source = fallback_text(facts), "fallback_ungrounded"
            except (LLMError, LLMUnavailable):
                text, source = fallback_text(facts), "fallback_llm_failed"
        msgs = [*state.get("messages", []), {"role": "user", "content": state["user_message"]},
                {"role": "assistant", "content": text}][-MAX_MESSAGES:]
        trace = make_trace(state, source, explain_model)
        if trace_sink is not None:
            try:
                trace_sink(trace)
            except Exception:  # noqa: BLE001 — 기록 실패가 대화를 막지 않게
                pass
        return {"response": text, "messages": msgs, "trace": trace}

    return (extract, update_profile, confirm, check_required, recommend, what_if, analyze, compare_previous, validate,
            respond, policy)


# ---------------------------------------------------------------- 분기

def route_after_update(state: AgentState) -> str:
    if state.get("status") != "ok":
        return "respond"
    if state.get("pending_commit"):
        return "confirm"
    return route_after_confirm(state)


def route_after_confirm(state: AgentState) -> str:
    if state.get("status") != "ok" or state.get("confirm_declined"):
        return "respond"
    intent = state.get("intent")
    if intent in ("explain", "smalltalk"):
        return "respond"
    if intent == "policy":
        return "policy"
    if intent == "compare_previous":
        return "compare_previous"
    if intent == "revert" and not state.get("profile_changed"):
        return "respond"
    return "check_required"


def route_after_required(state: AgentState) -> str:
    if state.get("pending_required_fields") or state.get("blocked"):
        return "respond"
    intent = state.get("intent")
    return "what_if" if intent == "what_if" else "analyze" if intent == "analyze" else "recommend"


def _timed(name: str, fn: Callable[[AgentState], AgentState]) -> Callable[[AgentState], AgentState]:
    """노드 실행 시간을 trace_steps 에 남긴다. extract 에서 새 턴의 기록을 시작한다."""
    def run(state: AgentState) -> AgentState:
        t = time.perf_counter()
        out = fn(state) or {}
        prev = [] if name == "extract" else list(state.get("trace_steps", []))
        return {**out, "trace_steps": [*prev, {"node": name, "ms": round((time.perf_counter() - t) * 1000)}]}
    return run


def build_graph(get_llm: Callable[[], AgentLLM], checkpointer=None,
                trace_sink: Callable[[dict[str, Any]], None] | None = None):
    (extract, update_profile, confirm, check_required, recommend, what_if, analyze, compare_previous, validate,
     respond, policy) = make_nodes(get_llm, trace_sink)
    g = StateGraph(AgentState)
    for name, fn in [("extract", extract), ("update_profile", update_profile), ("confirm", confirm),
                     ("check_required", check_required),
                     ("recommend", recommend), ("what_if", what_if), ("analyze", analyze),
                     ("compare_previous", compare_previous), ("policy", policy),
                     ("validate", validate), ("respond", respond)]:
        g.add_node(name, _timed(name, fn) if name != "respond" else fn)
    g.add_edge(START, "extract")
    g.add_edge("extract", "update_profile")
    g.add_conditional_edges("update_profile", route_after_update,
                            ["respond", "confirm", "compare_previous", "check_required", "policy"])
    g.add_conditional_edges("confirm", route_after_confirm, ["respond", "compare_previous", "check_required", "policy"])
    g.add_edge("policy", "respond")
    g.add_conditional_edges("check_required", route_after_required, ["respond", "what_if", "analyze", "recommend"])
    for n in ("recommend", "what_if", "analyze", "compare_previous"):
        g.add_edge(n, "validate")
    g.add_edge("validate", "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


# ---------------------------------------------------------------- 사실 · 설명 검증 · 대체 문장

def _label(k: str) -> str:
    return FACTOR_LABELS.get(k, k)


def _rec_facts(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    req = rec.get("request", {})
    return {
        "university": rec["university"],
        "priorities": [{"rank": i + 1, "name": _label(k), "weight_percent": round(rec["weights"].get(k, 0) * 100)}
                       for i, k in enumerate(rec["priorities"])],
        "budget_manwon": req.get("budget_manwon"),
        "desired_commute_min": req.get("desired_commute_min"),
        "desired_area_m2": req.get("desired_area_m2"),
        "candidates": rec["total_ranked"],
        "excluded": rec["excluded_count"],
        "top": [
            {
                "rank": t["rank"], "address": t.get("address"), "type": t.get("property_type"),
                "monthly_cost_manwon": t["monthly_cost_manwon"], "deposit_manwon": t["deposit_manwon"],
                "area_m2": t["area_m2"], "travel_min_one_way": t.get("travel_min"), "score": t["score"],
                "factor_scores": {_label(k): v for k, v in t["factor_scores"].items()},
                "over_budget": t["over_budget"],
                "walk_min_to_building": t.get("walk_min", {}),
                "same_house_ads_hidden": len(t.get("same_house_ids", [])),
            }
            for t in rec["top"]
        ],
        "engine_notes": rec["notes"],
        "excluded_by_place_condition": rec.get("exclusion_counts", {}),
    }


def _cmp_facts(cmp: dict[str, Any] | None, recs: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    if not cmp:
        return None
    addr = {t["property_id"]: t.get("address") for r in recs.values() if r for t in r["top"]}
    return {
        "before": cmp["label_before"], "after": cmp["label_after"],
        "candidates_before": cmp["total_before"], "candidates_after": cmp["total_after"],
        "changed_conditions": cmp["profile_diff"],
        "top_changes": [
            {"address": addr.get(c["property_id"], c["property_id"]), "rank_before": c["rank_before"],
             "score_before": c["score_before"], "rank_after": c["rank_after"], "score_after": c["score_after"]}
            for c in cmp["score_changes"]
        ],
    }


def build_facts(state: AgentState) -> dict[str, Any]:
    intent = state.get("intent")
    shown = state.get("scenario_recommendation") or (
        state.get("last_recommendation") if intent in ("recommend", "update", "revert", "explain") else None)
    if intent in ("compare_previous", "analyze"):
        shown = None
    cond = state.get("scenario_profile") or state.get("current_profile")
    return {
        "mode": "what_if" if state.get("scenario_profile") else (intent or "none"),
        "status": state.get("status", "ok"),
        "llm_error_kind": state.get("llm_error_kind"),
        "conditions": AgentProfile.model_validate(cond).model_dump(mode="json", exclude_defaults=True) if cond else {},
        "result": _rec_facts(shown),
        "comparison": _cmp_facts(state.get("comparison"), {
            "last": state.get("last_recommendation"), "prev": state.get("previous_recommendation"),
            "scen": state.get("scenario_recommendation")}),
        "analysis": state.get("analysis_facts"),
        "policy": _policy_facts(state.get("policy")),
        "missing_fields": state.get("pending_required_fields", []),
        "blocked": state.get("blocked"),
        "notes": state.get("notes", []),
        "limitations": [LISTING_NOTE, "도보시간은 Kakao 도보 경로(건물 대표 지점) 기준이며 대중교통은 반영하지 않습니다."],
        "asked_rank": (state.get("extraction") or {}).get("property_rank"),
        # 추천 방식 명세 (코드에서 생성). "왜 1위야?", "어떻게 추천해?"에 답하는 근거. 숫자 검증의 허용 값에도 들어간다
        "how_it_works": capability_spec((AgentProfile.model_validate(cond).university if cond else None)),
    }


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _nums(text: str) -> set[float]:
    return {float(x) for x in _NUM.findall(text.replace(",", ""))}


def numbers_grounded(text: str, facts: dict[str, Any]) -> bool:
    """설명에 나온 숫자가 모두 사실 JSON 에 있는지. 순서 표현(1~10)은 허용한다."""
    allowed = _nums(json.dumps(facts, ensure_ascii=False)) | {float(i) for i in range(0, 11)}
    return _nums(text) <= allowed


def _fmt(x: float | None) -> str:
    if x is None:
        return "정보 없음"
    return f"{x:g}"


def fallback_text(facts: dict[str, Any]) -> str:
    """LLM 없이 사실만으로 만든 문장 (LLM 실패·숫자 검증 실패·질문)."""
    lines: list[str] = []
    st = facts["status"]
    if st == "llm_unavailable":
        lines.append("AI 대화 기능이 아직 설정되지 않았습니다 (서버의 LLM 키·모델). 생활 프로필 화면에서 조건을 넣으면 추천은 그대로 받을 수 있어요.")
    elif st == "llm_error":
        lines.append(LLM_ERROR_TEXT.get(facts.get("llm_error_kind") or "other", LLM_ERROR_TEXT["other"])
                     + " 생활 프로필·매물 비교 화면의 추천은 그대로 쓸 수 있어요.")
    elif st == "error":
        lines.append("추천 결과를 확인하는 중 문제가 생겼습니다.")
    if facts.get("blocked"):
        lines.append(facts["blocked"])
    if facts.get("missing_fields"):
        lines.append("추천하려면 다음을 알려 주세요: " + ", ".join(facts["missing_fields"]) + ".")
    res = facts.get("result")
    if res and st == "ok":
        crit = ", ".join(f"{c['rank']}순위 {c['name']} {c['weight_percent']}%" for c in res["priorities"])
        lines.append(f"{res['university']} 기준, 조건을 통과한 매물 {res['candidates']}개 중 상위 매물입니다 ({crit}).")
        for t in res["top"]:
            lines.append(f"{t['rank']}. {t['address']} · 유지비 월 {_fmt(t['monthly_cost_manwon'])}만 원"
                         + (" (예산 초과)" if t["over_budget"] else "")
                         + f" · 보증금 {_fmt(t['deposit_manwon'])}만 원 · 통학 편도 {_fmt(t['travel_min_one_way'])}분"
                         f" · 면적 {_fmt(t['area_m2'])}㎡ · 점수 {_fmt(t['score'])}"
                         + (f" · 같은 집일 수 있는 광고 {t['same_house_ads_hidden']}건 더" if t.get("same_house_ads_hidden") else ""))
    an = facts.get("analysis")
    if an and st == "ok":
        lines.extend(analysis_lines(an))
    pol = facts.get("policy")
    if pol and st == "ok":
        lines.extend(policy_lines(pol))
    cmp = facts.get("comparison")
    if cmp and st == "ok":
        lines.append(f"{cmp['before']} {cmp['candidates_before']}개 → {cmp['after']} {cmp['candidates_after']}개.")
        def r(x: int | None) -> str:
            return "순위 밖" if x is None else f"{x}위"

        for c in cmp["top_changes"]:
            lines.append(f"- {c['address']}: {r(c['rank_before'])} → {r(c['rank_after'])}")
    for n in facts.get("notes", []):
        if st == "ok":
            lines.append(n)
    if res and st == "ok":
        lines.extend(res["engine_notes"])
        lines.append(LISTING_NOTE)
    if not lines:
        lines.append("학교, 월 유지비 상한, 우선순위(이동 시간·예산·면적), 희망 통학 시간·면적, 수업 듣는 건물을 알려 주시면 "
                     "매물을 추천해 드릴게요.")
    return "\n".join(lines)


def _policy_facts(pol: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pol:
        return None
    return {
        "available": pol["available"], "reason": pol.get("reason"), "query": pol["query"],
        "total_matched": pol.get("total_matched", 0),
        "items": [{k: it.get(k) for k in ("name", "summary", "support", "subcategory", "agency", "region", "target_age",
                                           "apply_period", "apply_url", "ref_url", "updated_at")} for it in pol["items"]],
        "source": pol["source"], "fetched_at": pol.get("fetched_at"), "official_links": pol["official_links"],
        "disclaimer": pol["disclaimer"],
    }


def policy_lines(pol: dict[str, Any]) -> list[str]:
    """정책 사실 → 문장 (LLM 없이). 자격을 판정하지 않는다."""
    links = ", ".join(f"{l['name']} {l['url']}" for l in pol["official_links"])
    if not pol["available"]:
        return [pol.get("reason") or "정책 데이터가 없습니다.", f"공식 사이트: {links}"]
    if not pol["items"]:
        return [f"'{pol['query']}'와 관련된 주거 정책을 찾지 못했습니다.", f"공식 사이트: {links}"]
    out = [f"'{pol['query']}' 관련 주거 정책 {len(pol['items'])}개입니다 (출처: {pol['source']}"
           + (f", 기준 {pol['fetched_at'][:10]})" if pol.get("fetched_at") else ")")]
    for it in pol["items"]:
        bits = [x for x in (it.get("agency"), it.get("region"), f"신청 {it['apply_period']}" if it.get("apply_period") else None) if x]
        out.append(f"- {it['name']}" + (f" ({' · '.join(bits)})" if bits else "")
                   + (f": {it['support'] or it['summary']}"[:160] if (it.get("support") or it.get("summary")) else "")
                   + (f" {it['apply_url'] or it['ref_url']}" if (it.get("apply_url") or it.get("ref_url")) else ""))
    out.append(pol["disclaimer"])
    return out


def analysis_lines(an: dict[str, Any]) -> list[str]:
    """분석 사실 → 문장 (LLM 없이). 숫자는 사실의 값만 쓴다."""
    out = [f"[{an['name']}] 참고 분석이며 추천 순위는 바꾸지 않습니다."]
    if an.get("error"):
        return [*out, an["error"]]
    k = an["kind"]
    if k == "pareto":
        out.append(f"후보 {an['candidates']}개 중 다른 매물보다 모든 면({', '.join(an['objectives'])})에서 못하지 않은 매물은 "
                   f"{an['frontier_count']}개입니다.")
        for t in an["top_k_on_frontier"]:
            out.append(f"- {t['rank']}위 {t['address']}: " + ("해당" if t["pareto"] else f"{t['dominated_by']}보다 모든 면에서 같거나 못함"))
    elif k == "tradeoff":
        ref = an["reference"]
        out.append(f"기준: {ref['rank']}위 {ref['address']} (유지비 {_fmt(ref['monthly_cost_manwon'])}만 원, "
                   f"통학 {_fmt(ref['travel_min'])}분)")
        for st in an["pay_more_for_commute"]:
            if st["best"] and st["travel_saved_min"]:
                out.append(f"- 월 {_fmt(st['extra_budget_manwon'])}만 원까지 더 내면: {st['best']['address']} "
                           f"(통학 {_fmt(st['travel_saved_min'])}분 단축)")
        sim = an["similar_commute_cheaper"]
        if sim["best"]:
            out.append(f"- {sim['label']}: {sim['best']['address']} (월 {_fmt(sim['saved_manwon'])}만 원 절약)")
    elif k == "sensitivity":
        for kd in an["by_kind"][:3]:
            if kd["mean_top_retention"] is not None:
                out.append(f"- {kd['label']}을(를) 바꾸면 상위 {an['top_k']}개 중 평균 "
                           f"{_fmt(round(kd['mean_top_retention'] * 100))}%가 유지됩니다.")
        for st in an["stable"][:3]:
            out.append(f"- {st['address']}: {st['total']}개 조건 중 {st['in_top']}개에서 상위권")
    elif k == "relaxation":
        out.append(f"지금 후보 {an['baseline_candidates']}개 (목표 {an['target_count']}개).")
        for o in an["minimal"]:
            out.append(f"- {o['label']} → 후보 {o['candidates']}개")
    elif k == "market":
        if not an["available"]:
            out.append(an.get("reason") or "비교할 수 없습니다.")
        else:
            out.append(f"{an['rank']}위 {an['address']}: 월세 {_fmt(an['listing_monthly_rent_manwon'])}만 원, "
                       f"비교 실거래 {an['n']}건의 월세 중앙값 {_fmt(an['rent_median'])}만 원"
                       + (f" → {an['level']}" if an.get("level") else " (표본이 적어 수준은 판단하지 않음)"))
        out.extend(an.get("notes", [])[:2])
    elif k == "building":
        if not an["available"]:
            out.append(an.get("reason") or "정보가 없습니다.")
        else:
            out.append(f"{an['rank']}위 {an['address']} (대장 기준): 주용도 {an.get('main_purpose') or '정보 없음'}, "
                       f"사용승인 {an.get('use_approval_date') or '정보 없음'}, 지상 {_fmt(an.get('floors_above'))}층, "
                       f"승강기 {_fmt(an.get('elevators'))}대, 주차 {_fmt(an.get('parking'))}대")
        out.extend(an.get("notes", []))
    elif k == "amenities":
        if not an["available"]:
            out.append(an.get("reason") or "정보가 없습니다.")
        else:
            near = ", ".join(f"{n} {d}m" for n, d in an["nearest_m"].items() if d is not None)
            out.append(f"{an['rank']}위 {an['address']} 주변 가장 가까운 곳(직선거리): {near or '500m 안 없음'}")
    return out


TOOLS_BY_NODE = {"update_profile": "building·profile 검증", "confirm": "사용자 확인", "recommend": "recommendation", "what_if": "recommendation×2 + comparison",
                 "compare_previous": "comparison", "analyze": "recommendation + analysis", "policy": "policy 안내",
                 "validate": "result 검증"}


def make_trace(state: AgentState, explanation_source: str, explain_model: str | None) -> dict[str, Any]:
    """한 턴의 기록: 어떤 요청이 어떤 의도로 읽혀 어떤 도구로 처리됐는지 (CLAUDE.md 7장)."""
    steps = list(state.get("trace_steps", []))
    ex = state.get("extraction") or {}
    rec = state.get("scenario_recommendation") or state.get("last_recommendation")
    return {
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "session_id": state.get("session_id"),
        "message": state.get("user_message"),
        "intent": state.get("intent"),
        "changes": {k: v for k, v in (ex.get("changes") or {}).items() if v is not None},
        "unsupported": ex.get("unsupported") or [],
        "status": state.get("status"),
        "llm_error_kind": state.get("llm_error_kind"),
        "nodes": steps,
        # update_profile 은 조건 추출이 성공했을 때만 실제로 일한다 (실패 턴은 건너뜀)
        "tools": [TOOLS_BY_NODE[s["node"]] for s in steps
                  if s["node"] in TOOLS_BY_NODE and (s["node"] != "update_profile" or state.get("extraction"))],
        "ms_total": sum(s["ms"] for s in steps),
        "extract_model": (state.get("trace") or {}).get("extract_model"),
        "explain_model": explain_model,
        "explanation_source": explanation_source,
        "missing_fields": state.get("pending_required_fields", []),
        "profile_changed": bool(state.get("profile_changed")),
        "top": [t["property_id"] for t in rec["top"]] if rec and state.get("status") == "ok" else [],
        "analysis": (state.get("analysis") or {}).get("kind"),
    }


FIELD_LABELS = {
    "university": "학교", "budget_manwon": "월 유지비 상한(만원)", "deposit_budget_manwon": "보증금 상한(만원)",
    "department": "학과", "department_buildings": "수업 건물", "grade": "학년", "priorities": "우선순위",
    "desired_commute_min": "희망 통학 시간(분)", "desired_area_m2": "희망 면적(㎡)", "larger_area": "넓은 방",
    "timetable": "시간표", "exclusions": "장소 조건",
}


def _show(v: Any) -> str:
    if v is None or v == [] or v == {}:
        return "없음"
    if isinstance(v, list) and v and isinstance(v[0], dict) and "day" in v[0]:
        return f"{len(v)}칸"
    if isinstance(v, list) and v and isinstance(v[0], dict) and "conditions" in v[0]:
        return ", ".join(e.get("source_text") or "조건" for e in v)
    if isinstance(v, list):
        return ", ".join(str(FACTOR_LABELS.get(x, x)) for x in v)
    return f"{v:g}" if isinstance(v, float) else str(v)


def confirm_question(before: AgentProfile, after: AgentProfile, kind: str) -> str | None:
    """기존 입력이 지워지는 변경이면 확인 질문, 아니면 None.

    - update: 학교가 바뀌면서 그 학교 기준 수업 건물·시간표가 지워질 때
    - revert: 직전 조건으로 되돌릴 때 두 항목 이상이 바뀌면
    """
    diff = tools.profile_diff(before, after)
    if kind == "update":
        added = [e for e in after.exclusions if e not in before.exclusions]
        if added:
            return ("다음 장소 조건으로 매물을 빼고 추천할까요? (네 / 아니요)\n" + "\n".join(tools.exclusion_lines(added))
                    + "\n상가정보(소상공인시장진흥공단) 등록 업소 기준이며 거리는 직선거리입니다.")
        lost = before.university is not None and after.university != before.university and             (before.department_buildings or before.timetable)
        if not lost:
            return None
        gone = ", ".join(filter(None, [_show(before.department_buildings) if before.department_buildings else "",
                                       f"시간표 {len(before.timetable)}칸" if before.timetable else ""]))
        return (f"학교를 {before.university} → {after.university}(으)로 바꾸면 {before.university} 기준 "
                f"입력({gone})이 지워져요. 바꿀까요? (네 / 아니요)")
    if kind == "revert" and len(diff) >= 2:
        lines = [f"- {FIELD_LABELS.get(k, k)}: {_show(v['before'])} → {_show(v['after'])}" for k, v in diff.items()]
        return "직전 조건으로 되돌리면 다음이 바뀌어요. 되돌릴까요? (네 / 아니요)\n" + "\n".join(lines)
    return None


_YES = ("네", "예", "응", "어", "ㅇㅇ", "ㅇ", "좋아", "그래", "맞아", "바꿔", "되돌려", "ok", "okay", "yes", "y", "넵", "넹", "웅")
_NO = ("아니", "아뇨", "ㄴㄴ", "ㄴ", "취소", "싫어", "그대로", "no", "n", "안 바꿔", "됐어")


def yes_no(message: str) -> bool | None:
    """확인 질문에 대한 답 (규칙 기반, LLM 안 씀). 판단할 수 없으면 None."""
    t = re.sub(r"[\s!.~,?]+", " ", message.strip().lower()).strip()
    if not t:
        return None
    if any(t == w or t.startswith(w + " ") or t.startswith(w) and len(t) <= len(w) + 2 for w in _NO):
        return False
    if any(t == w or t.startswith(w + " ") or t.startswith(w) and len(t) <= len(w) + 2 for w in _YES):
        return True
    return None


def run_turn(graph: Any, session_id: str, message: str, fe_profile: dict[str, Any] | None) -> AgentState:
    """한 턴 실행. 확인 질문을 기다리는 중이면 답(네/아니요)으로 이어서 실행하고,
    답이 아니면 변경을 취소한 뒤 새 메시지로 처리한다."""
    cfg = {"configurable": {"thread_id": session_id}}
    snap = graph.get_state(cfg)
    if snap.next and "confirm" in snap.next:
        ans = yes_no(message)
        if ans is not None:
            return graph.invoke(Command(resume=ans), config=cfg)
        graph.invoke(Command(resume=False), config=cfg)
    return graph.invoke({"user_message": message, "session_id": session_id, "fe_profile": fe_profile}, config=cfg)


def to_response_parts(state: AgentState) -> dict[str, Any]:
    """그래프 최종 상태 → API 응답 필드."""
    if state.get("__interrupt__"):
        q = state["__interrupt__"][0].value
        return {
            "assistant_message": q.get("question") or "바꿀까요? (네 / 아니요)",
            "updated_profile": _profile(state["current_profile"]) if state.get("current_profile") else None,
            "profile_changed": False, "recommendation_results": None, "comparison_results": None, "analysis_results": None,
            "policy_results": None,
            "missing_fields": [], "intent": state.get("intent"), "status": "confirm",
            "pending_change": q.get("diff"), "trace": None,
        }
    rec = state.get("scenario_recommendation") or (
        state.get("last_recommendation") if state.get("intent") in ("recommend", "update", "revert", "explain") else None)
    cmp = state.get("comparison")
    return {
        "assistant_message": state.get("response", ""),
        "updated_profile": _profile(state["current_profile"]) if state.get("current_profile") else None,
        "profile_changed": bool(state.get("profile_changed")),
        "recommendation_results": RecSummary.model_validate(rec) if rec and state.get("status") == "ok" else None,
        "comparison_results": Comparison.model_validate(cmp) if cmp and state.get("status") == "ok" else None,
        "analysis_results": AnalysisOut.model_validate(state["analysis"])
        if state.get("analysis") and state.get("status") == "ok" else None,
        "policy_results": state.get("policy") if state.get("status") == "ok" else None,
        "missing_fields": state.get("pending_required_fields", []),
        "intent": state.get("intent"),
        "status": state.get("status", "ok"),
        "trace": state.get("trace"),
        "pending_change": None,
    }
