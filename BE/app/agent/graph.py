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
from app.agent.schemas import AgentProfile, AnalysisOut, Comparison, ProfileChanges, RecSummary, TurnExtraction
from app.exclusions import josa, tool_catalog
from app.agent.spec import capability_spec
from app.config import _read
from app.data import load_properties
from app.scoring import FACTOR_LABELS

MAX_MESSAGES = 20
LLM_ERROR_TEXT = {
    "busy": "AI 서버가 지금 혼잡해 응답을 받지 못했습니다. 잠시 뒤 다시 보내 주세요.",
    "quota": "AI 사용 한도를 넘어 지금은 답할 수 없습니다. 한도가 풀린 뒤 다시 시도해 주세요.",
    "too_long": "대화나 메시지가 너무 길어 AI 가 처리하지 못했습니다. 메시지를 짧게 나눠 보내 주세요.",
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
    chat_mode: str | None                  # FE 모드 배너: recommend | policy | None(자동 판별)
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
    llm_error_detail: str | None  # 원인 사슬 (키는 가림), 배포 진단용
    response: str
    lead_text: str | None       # 답변 맨 앞에 붙는 서버 문장 ('바뀐 것' — 제외 조건을 바로 적용했을 때)
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
            "analysis": None, "analysis_facts": None, "policy": None, "profile_changed": False, "blocked": None, "status": "ok", "llm_error_kind": None, "llm_error_detail": None, "response": "", "trace": None, "lead_text": None,
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
            detail = llm_error_detail(e)
            print(f"[agent] LLM 오류 ({getattr(e, 'kind', 'other')}): {detail}", flush=True)  # 배포 로그용 (키는 가림)
            out.update(status="llm_error", notes=[str(e)], llm_error_kind=getattr(e, "kind", "other"),
                       llm_error_detail=detail)
            return out
        msg = state["user_message"]
        if "시간표" in msg and any(w in msg for w in PHOTO_WORDS):  # 대화창에서는 사진을 받지 않는다 (R-202)
            ex = ex.model_copy(update={"intent": "out_of_scope", "scope_topic": "timetable_photo"})
        if ex.exclusion_request and ex.intent in ("smalltalk", "explain"):  # 기호만 말한 턴 = 조건 변경 (R-201)
            ex = ex.model_copy(update={"intent": "update"})
        ex, guide = apply_chat_mode(ex, state.get("chat_mode"))
        if guide:
            out["blocked"] = guide
        out.update(intent=ex.intent, extraction=_dump(ex))
        return out

    def plan_exclusion(base: AgentProfile, request: str) -> tuple[AgentProfile, list[str], list[str]]:
        """장소가 아닌 기호 → LLM 확인 계획 → 서버 검증·병합 (agent-integrated 에서 가져옴, 규칙 문서 5장).
        → (조건을 합친 프로필, 답변 앞에 붙일 줄, notes). 남는 매물 0개 확인·'바뀐 것'은 apply_exclusions_now 가 한다."""
        props = [x for x in load_properties() if x.university == base.university] if base.university else list(load_properties())
        ctx = {"catalog": tool_catalog(props or None),
               "exclusions": [c.describe() for e in base.exclusions for c in e.conditions]}
        try:
            draft = get_llm().plan(request, ctx)
        except (LLMError, LLMUnavailable):
            return base, [], ["기호를 확인 조건으로 바꾸지 못했어요. 잠시 뒤 다시 말해 주세요."]
        plan, notes = tools.plan_from_draft(draft, request, props or None)
        if not plan.conditions:  # 현장 확인·확인 불가만 있음 → 매물은 빼지 않고 안내만
            return base, [plan.applied_text(None)], notes
        merged, replaced = tools.merge_plans(list(base.exclusions), plan)
        extra = plan._tail_lines()
        if replaced:
            extra.append(f"(기존 조건 {', '.join(replaced)}{josa(replaced[-1], '은', '는')} 새 기준으로 바꿨어요.)")
        return base.model_copy(update={"exclusions": merged}), extra, notes

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
            extra: list[str] = []
            if ex.exclusion_request:
                new, extra, n = plan_exclusion(new, ex.exclusion_request)
                notes += n
            # 제외 조건은 확인 질문 없이 바로 적용하고 '바뀐 것'을 답변 맨 앞에 (11/15 팀 결정).
            # 적용하면 남는 매물이 0개일 때만 그 조건을 넣지 않고 되묻는다.
            added = [e for e in new.exclusions if e not in current.exclusions]
            if added:
                new, lead = apply_exclusions_now(current, new, added)
                out["lead_text"] = "\n".join([lead, *extra])
            elif extra:
                out["lead_text"] = "\n".join(extra)
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
            if ex.exclusion_request:  # 가정에도 기호를 반영 (저장하지 않는다)
                scen, extra, n = plan_exclusion(scen, ex.exclusion_request)
                notes += n
                if extra:
                    out["lead_text"] = "\n".join(extra)
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
            same = last and last.get("request") == tools.request_key(current)
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
        same = last and last.get("request") == tools.request_key(current)
        rec = RecSummary.model_validate(last) if same else tools.run_recommendation(current)
        if not same:
            out["last_recommendation"] = _dump(rec)
        res, facts = tools.run_analysis(ex.analysis, current, rec, ex.property_rank)
        out.update(analysis=_dump(res), analysis_facts={"name": tools.ANALYSIS_LABELS[ex.analysis], **facts})
        return out

    def policy(state: AgentState) -> AgentState:
        """주거 지원 정책 안내 (공식 데이터 검색만, 자격 판정 없음). 프로필 필수 조건을 묻지 않는다."""
        from app.policy import compare_policies, contract_checklist, search_policies

        ex = TurnExtraction.model_validate(state["extraction"])
        kind = ex.policy_kind or "info"
        if kind == "contract_safety":
            res = contract_checklist()
        elif kind == "compare" and len(ex.policy_names) >= 2:
            res = compare_policies(ex.policy_names)
        else:
            # 지역을 말하지 않으면 서비스 지역(4개 대학 모두 서울) + 전국 정책 (2026-11-16)
            regions = ex.policy_regions or ["서울"]
            res = search_policies(ex.policy_query or state["user_message"], regions=regions,
                                  kind="info" if kind == "compare" else kind)
            if not ex.policy_regions:
                res.notes.append("지역을 말씀하지 않아 서울과 전국 정책을 먼저 찾았어요. 다른 지역은 지역 이름을 함께 말해 주세요.")
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
        elif state.get("intent") == "out_of_scope":  # 판단하지 않고 안내처 (R-701)
            topic = (state.get("extraction") or {}).get("scope_topic") or "unrelated"
            text, source = REFUSAL.get(topic, REFUSAL["unrelated"]), "template_refusal"
        elif state.get("blocked") or state.get("pending_required_fields"):
            text, source = fallback_text(facts), "template_question"
        elif not llm_explain_enabled():
            text, source = fallback_text(facts), "template"
        elif _policy_without_data(state):
            # 정책 데이터가 없을 때 LLM 이 '일시적 지연' 처럼 원인을 바꿔 말하지 않게 고정 문장으로 답한다
            text, source = fallback_text(facts), "template_no_policy_data"
        else:
            try:
                llm = explain_llm(get_llm)
                text = llm.explain(state["user_message"], facts)
                explain_model = getattr(llm, "last_model", None)
                source = "llm"
                if not text or not numbers_grounded(text, facts):
                    text, source = fallback_text(facts), "fallback_ungrounded"
                elif any(w in text for w in FORBIDDEN_PHRASES):  # 보장·판단하는 말 (R-702)
                    text, source = fallback_text(facts), "fallback_forbidden"
            except (LLMError, LLMUnavailable):
                text, source = fallback_text(facts), "fallback_llm_failed"
        if state.get("lead_text"):
            text = f"{state['lead_text']}\n\n{text}" if text else state["lead_text"]
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

# 범위 밖 안내 (규칙 문서 R-701, agent-integrated 에서 가져와 정책 안내·계약 전 확인으로 연결)
OFFER = "대신 조건에 맞는 매물을 더 찾아 드릴 수 있어요."
REFUSAL = {
    "contract_legal": "계약·특약·보증금 반환 같은 법률 문제는 제가 판단할 수 없어요. 대한법률구조공단(국번 없이 132)이나 "
                      "주택임대차분쟁조정위원회에 상담해 보세요. 계약 전에 확인할 것은 챗봇 위쪽 '정책 안내' 모드에서 "
                      "'계약 전에 확인할 것'을 물어보시면 단계별로 알려 드려요.",
    "fraud_registry": "계약해도 안전한지, 사기인지는 제가 판단할 수 없어요. 계약 전에 인터넷등기소에서 등기부등본을 떼어 "
                      "집주인과 권리 관계를 확인하고, 공인중개사 등록 여부도 확인해 보세요. 매물 상세의 '계약 전 확인' 카드에 "
                      "그 매물의 실거래 비교와 건축물대장 정보가 함께 있어요.",
    "landlord": "집주인이나 관리 상태는 데이터로 알 수 없어요. 집 보러 갈 때 직접 확인해 보세요. " + OFFER,
    "listing_guarantee": "광고 매물이라 실제로 있는지, 지금 계약할 수 있는지는 보장할 수 없어요. 원문 링크에서 확인해 주세요. " + OFFER,
    "market_forecast": "시세가 오르거나 내릴지는 예측해 드리지 않아요. 대신 매물 상세에서 주변 실거래와 비교한 값을 볼 수 있어요.",
    "unsupported_region": "지금은 " + "·".join(["이화여자대학교", "고려대학교", "서강대학교", "서울과학기술대학교"])
                          + " 인근 원룸·오피스텔 월세 매물만 추천할 수 있어요. 이 중 한 곳을 알려 주시면 바로 찾아 드릴게요.",
    "unrelated": "저는 대학가 자취방 추천과 주거 정책 안내를 도와드려요. 학교·예산·통학 시간 같은 조건을 말해 주시면 매물을 찾아 드릴게요.",
    "timetable_photo": "대화창에서는 시간표 사진을 받지 않아요. 생활 프로필의 '시간표 캡처로 불러오기'에서 사진을 올리면 수업 칸을 읽어 "
                       "확인한 뒤 넣을 수 있어요. \"월수 9시부터 10시 반까지 ECC\"처럼 말해 주셔도 돼요.",
}
PHOTO_WORDS = ("사진", "이미지", "캡처", "캡쳐", "스크린샷")
# 보장·판단하는 말 (R-702). 설명 문장에 나오면 사실 문장으로 바꾼다
FORBIDDEN_PHRASES = ("안전합니다", "안전해요", "사기가 아닙니다", "사기 아니에요", "계약해도 됩니다", "계약해도 돼요",
                     "사기 위험이 적", "대상자입니다", "받을 수 있습니다")

MODE_GUIDE_POLICY = ("주거 지원 정책·계약 질문은 챗봇 위쪽 배너에서 '정책 안내' 모드로 바꾼 뒤 물어봐 주세요. "
                     "'매물 추천' 모드에서는 매물 추천·조건 변경·분석만 다룹니다.")


def apply_chat_mode(ex: TurnExtraction, mode: str | None) -> tuple[TurnExtraction, str | None]:
    """FE 모드 배너에 맞춰 의도를 고정한다. mode 가 없으면 LLM 판별 그대로 (기존 동작).
    - policy: 인사(smalltalk) 외에는 모두 정책 안내로 보내고, 프로필 조건은 바꾸지 않는다.
    - recommend: 정책 질문이면 정책 검색을 하지 않고 모드 전환을 안내한다."""
    if mode == "policy" and ex.intent == "out_of_scope":
        if ex.scope_topic in ("contract_legal", "fraud_registry"):  # R-703: 거절 대신 계약 안전 점검 목록
            return ex.model_copy(update={"intent": "policy", "policy_kind": "contract_safety", "changes": ProfileChanges()}), None
        return ex, None
    if mode == "policy" and ex.intent not in ("policy", "smalltalk"):
        return ex.model_copy(update={"intent": "policy", "changes": ProfileChanges()}), None
    if mode == "recommend" and ex.intent == "policy":
        return ex.model_copy(update={"intent": "smalltalk"}), MODE_GUIDE_POLICY
    return ex, None


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
    if intent in ("explain", "smalltalk", "out_of_scope"):
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
                **({"walk_min_to_frequent_place": t["frequent_walk_min"]} if t.get("frequent_walk_min") else {}),
            }
            for t in rec["top"]
        ],
        "engine_notes": rec["notes"],
        "excluded_by_place_condition": rec.get("exclusion_counts", {}),
        **({"frequent_places": rec["frequent"]} if rec.get("frequent") else {}),
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
                         + (f" · 같은 집일 수 있는 광고 {t['same_house_ads_hidden']}건 더" if t.get("same_house_ads_hidden") else "")
                         + "".join(f" · {k}까지 도보 {'미확인' if v is None else _fmt(v) + '분'}"
                                   for k, v in (t.get("walk_min_to_frequent_place") or {}).items()))
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


def _policy_without_data(state: AgentState) -> bool:
    pol = state.get("policy")
    return bool(pol) and pol.get("kind") != "contract_safety" and not pol.get("available")


def _policy_facts(pol: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pol:
        return None
    return {
        "available": pol["available"], "reason": pol.get("reason"), "query": pol["query"],
        "kind": pol.get("kind", "info"), "regions": pol.get("regions", []),
        "total_matched": pol.get("total_matched", 0),
        "items": [{k: it.get(k) for k in ("name", "summary", "support", "subcategory", "agency", "region", "target_age",
                                           "income_text", "apply_period", "apply_method", "documents", "screening",
                                           "apply_url", "ref_url", "updated_at", "fetched_at", "status_note")}
                  for it in pol["items"]],
        "missing_names": pol.get("missing_names", []), "notes": pol.get("notes", []),
        "checklist": pol.get("checklist", []), "checklist_source": pol.get("checklist_source"),
        "source": pol["source"], "fetched_at": pol.get("fetched_at"), "official_links": pol["official_links"],
        "disclaimer": pol["disclaimer"],
    }


def _clip(text: str, n: int) -> str:
    """길면 n 자 안의 마지막 문장 끝에서 자른다 (문장 중간에서 끊겨 뜻이 바뀌지 않게)."""
    if len(text) <= n:
        return text
    cut = text.rfind(". ", 0, n)
    return (text[:cut + 1] if cut > n // 3 else text[:n].rstrip()) + " (자세한 내용은 공식 페이지)"


def policy_lines(pol: dict[str, Any]) -> list[str]:
    """정책 사실 → 문장 (LLM 없이). 자격을 판정하지 않는다."""
    links = ", ".join(f"{l['name']} {l['url']}" for l in pol["official_links"])
    kind = pol.get("kind", "info")
    notes = list(pol.get("notes", []))
    if kind == "contract_safety":
        out = ["집 계약 때 단계별로 확인할 것들이에요."]
        for sec in pol.get("checklist", []):
            out.append(f"[{sec['phase']}]")
            for it in sec["items"]:
                out.append(f"- {it['text']}" + (f" ({it['link']['name']} {it['link']['url']})" if it.get("link") else ""))
        return [*out, *notes, pol.get("checklist_source") or pol["disclaimer"]]
    if not pol["available"]:
        return [*notes, pol.get("reason") or "정책 데이터가 없습니다.", f"공식 사이트: {links}"]
    if not pol["items"]:
        return [*notes, f"'{pol['query']}'와 관련된 주거 정책을 찾지 못했습니다.", f"공식 사이트: {links}"]
    out = [*notes[:1], f"'{pol['query']}' 관련 주거 정책 {len(pol['items'])}개입니다 (출처: {pol['source']}"
           + (f", 기준 {pol['fetched_at'][:10]})" if pol.get("fetched_at") else ")")]
    for it in pol["items"]:
        bits = [x for x in (it.get("agency"), it.get("region"),
                            f"연령 {it['target_age']} (공고 기준)" if it.get("target_age") else None,
                            f"신청 {it['apply_period']}" if it.get("apply_period") else None) if x]
        out.append(f"- {it['name']}" + (f" ({' · '.join(bits)})" if bits else "")
                   + (f": {_clip(it['support'] or it['summary'], 240)}" if (it.get("support") or it.get("summary")) else "")
                   + (f" {it['apply_url'] or it['ref_url']}" if (it.get("apply_url") or it.get("ref_url")) else ""))
        if it.get("status_note"):
            out.append(f"  · 참고: {_clip(it['status_note'], 200)}")
        if kind == "eligibility" and it.get("income_text"):
            out.append(f"  · 소득 조건 (공고 표기): {_clip(it['income_text'], 240)}")
        if kind == "apply":
            for label, key in (("신청 방법", "apply_method"), ("제출 서류", "documents"), ("심사·발표", "screening")):
                out.append(f"  · {label}: {(it.get(key) or '공식 데이터에 표기 없음 → 공식 페이지 확인')[:200]}")
    out.extend(notes[1:])
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
    elif k == "excluded":
        out.append(f"전체 {an['total']}개 중 {an['ranked']}개가 조건을 통과했고 {an['excluded_count']}개가 빠졌습니다.")
        for s in an["stages"]:
            sub = f" ({', '.join(f'{c} {n}개' for c, n in s['by_condition'].items())})" if s["by_condition"] else ""
            out.append(f"- {s['label']}: {s['count']}개{sub}")
            for ex in s["examples"]:
                out.append(f"  · {ex['address'] or ex['property_id']}: {ex['reason']}")
        if an["not_recommended"]:
            out.append("조건은 통과했지만 지금 우선순위에서 순위가 밀린 매물:")
            for n in an["not_recommended"]:
                out.append(f"- {n['address'] or n['property_id']} (다른 우선순위에서는 {n['best_rank_elsewhere']}위): {n['message']}")
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


_SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")


def llm_error_detail(e: BaseException) -> str:
    """LLM 오류의 원인 사슬 (예외 종류: 메시지) — 배포에서 원인을 보려고. 키처럼 긴 문자열은 가린다."""
    parts: list[str] = []
    cur: BaseException | None = e
    while cur is not None and len(parts) < 4:
        parts.append(f"{type(cur).__name__}: {str(cur)[:200]}")
        cur = cur.__cause__ or cur.__context__
    return _SECRET_RE.sub("***", " <- ".join(parts))[:700]


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
        "llm_error_detail": state.get("llm_error_detail"),
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


def apply_exclusions_now(current: AgentProfile, new: AgentProfile, added: list) -> tuple[AgentProfile, str]:
    """새 장소 제외 조건을 적용해 본다 → (저장할 프로필, 답변 맨 앞 문장).

    빠진 수·남은 수는 같은 엔진으로 조건 없이/있이 추천을 돌려 센다 (상가정보, 외부 호출 없음).
    필수 조건이 아직 없어 추천을 돌릴 수 없으면 개수 없이 적용만 알린다.
    """
    without = new.model_copy(update={"exclusions": [e for e in new.exclusions if e not in added]})
    places = any(c.uses_places for e in added for c in e.conditions)
    lines = [f"바뀐 것: {'장소 ' if places else ''}제외 조건을 추가했어요.", *tools.exclusion_lines(added)]
    if not tools.missing_fields(new) and tools.university_listing_count(new.university):
        before = tools.run_recommendation(without)
        after = tools.run_recommendation(new)
        if after.total_ranked == 0 and before.total_ranked > 0:
            return without, "\n".join([
                f"이 {'장소 ' if places else ''}조건을 적용하면 남는 매물이 없어서 아직 적용하지 않았어요.", *tools.exclusion_lines(added),
                "거리나 개수 기준을 바꾸거나 다른 조건을 풀어 볼까요?"])
        lines.append(f"→ {before.total_ranked}개 중 {before.total_ranked - after.total_ranked}개가 빠지고 "
                     f"{after.total_ranked}개가 남았어요.")
    if places:
        lines.append("상가정보(소상공인시장진흥공단) 등록 업소 기준, 직선거리예요.")
    lines.append("조건을 빼려면 \"○○ 조건 빼 줘\"라고 하거나 비교 화면의 장소 조건 칩에서 지우면 돼요.")
    return new, "\n".join(lines)


def confirm_question(before: AgentProfile, after: AgentProfile, kind: str) -> str | None:
    """기존 입력이 지워지는 변경이면 확인 질문, 아니면 None.

    - update: 학교가 바뀌면서 그 학교 기준 수업 건물·시간표가 지워질 때
    - revert: 직전 조건으로 되돌릴 때 두 항목 이상이 바뀌면
    (장소 제외 조건은 묻지 않고 바로 적용한다 — apply_exclusions_now)
    """
    diff = tools.profile_diff(before, after)
    if kind == "update":
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


def run_turn(graph: Any, session_id: str, message: str, fe_profile: dict[str, Any] | None,
             mode: str | None = None) -> AgentState:
    """한 턴 실행. 확인 질문을 기다리는 중이면 답(네/아니요)으로 이어서 실행하고,
    답이 아니면 변경을 취소한 뒤 새 메시지로 처리한다."""
    cfg = {"configurable": {"thread_id": session_id}}
    snap = graph.get_state(cfg)
    if snap.next and "confirm" in snap.next:
        ans = yes_no(message)
        if ans is not None:
            return graph.invoke(Command(resume=ans), config=cfg)
        graph.invoke(Command(resume=False), config=cfg)
    return graph.invoke({"user_message": message, "session_id": session_id, "fe_profile": fe_profile,
                         "chat_mode": mode}, config=cfg)


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
