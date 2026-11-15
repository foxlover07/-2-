"""에이전트 (LLM + LangGraph, 통합안 엔진). 실제 LLM 은 부르지 않는다 — ScriptedLLM 으로 추출 결과를 정해 둔다.

필수 시나리오 (완전 입력, 정보 부족, 예산·대학·우선순위 변경, 되돌리기, what-if, 없는 건물, 도보시간 누락,
계산 불가 요청, LLM 장애, 엔진 결과 일치) + 분석 도구 6종 + 설명 숫자 검증 + API.
"""

import csv
import io
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import data
from app.agent.graph import build_graph, numbers_grounded
from app.agent.llm import LLMError, ScriptedLLM
from app.agent.schemas import AgentProfile, ProfileChanges, TurnExtraction
from app.scoring import RecommendRequest, recommend

EWHA = "이화여자대학교"
KU = "고려대학교"
KU_SPOTS = {"ECC": "정경관", "이화·포스코관": "LG포스코경영관", "학관": "문과대학 서관", "공과대학": "신공학관"}


@pytest.fixture(autouse=True)
def two_universities(monkeypatch, tmp_path):
    """PoC 10건(이대) + 같은 매물을 고려대로 바꾼 10건 (ku:P010 은 신공학관 도보시간 없음)."""
    src = data.DEFAULT_CSV_PATH.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(src)))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    for r in rows:
        r = dict(r, property_id="ku:" + r["property_id"], university=KU)
        for i in range(1, 5):
            r[f"spot{i}_name"] = KU_SPOTS.get(r[f"spot{i}_name"], r[f"spot{i}_name"])
            if r["property_id"] == "ku:P010" and r[f"spot{i}_name"] == "신공학관":
                r[f"spot{i}_walk_min"] = ""
        w.writerow(r)
    ku = tmp_path / "ku.csv"
    ku.write_text(out.getvalue(), encoding="utf-8")
    monkeypatch.setenv("DATA_CSV_PATH", f"{data.DEFAULT_CSV_PATH},{ku}")
    data.load_properties.cache_clear()
    yield
    data.load_properties.cache_clear()


def X(intent, unsupported=None, rank=None, analysis=None, **changes) -> TurnExtraction:
    return TurnExtraction(intent=intent, changes=ProfileChanges(**changes), unsupported=unsupported or [],
                          property_rank=rank, analysis=analysis)


class Chat:
    def __init__(self, extractions, explanations=None):
        self.llm = ScriptedLLM(extractions, explanations)
        self.graph = build_graph(lambda: self.llm)
        self.cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    def say(self, msg, fe_profile=None):
        return self.graph.invoke({"user_message": msg, "session_id": "t", "fe_profile": fe_profile}, config=self.cfg)


FULL = dict(university="이대", budget_manwon=70, department="공대", department_buildings=["공대"],
            priorities=["travel", "cost"], desired_commute_min=15, desired_area_m2=18)


def direct_req(**kw) -> RecommendRequest:
    base = dict(university=EWHA, department="공대", department_buildings=["공과대학"], grade=1,
                priorities=("travel", "cost"), budget_manwon=70, desired_commute_min=15, desired_area_m2=18)
    return RecommendRequest(**{**base, **kw})


def direct(**kw):
    props = data.load_properties()
    return recommend(props, direct_req(**kw), travel=data.load_travel_times(props))


# 1, 12 -----------------------------------------------------------------------

def test_full_input_matches_direct_engine():
    c = Chat([X("recommend", **FULL)])
    s = c.say("이화여대 공대생이고 월세랑 관리비 합쳐서 70만 원 이하. 통학이 1순위, 돈이 2순위. 걸어서 15분, 18㎡")
    assert s["status"] == "ok" and s["current_profile"]["university"] == EWHA
    assert s["current_profile"]["department_buildings"] == ["공과대학"]  # '공대' → 표준 이름
    rec = s["last_recommendation"]
    d = direct()
    assert rec["total_ranked"] == len(d.ranked) and rec["excluded_count"] == len(d.excluded)
    assert [(t["property_id"], t["score"]) for t in rec["top"]] == [(r.property_id, r.score) for r in d.ranked[:3]]
    assert {pid: tuple(v) for pid, v in rec["ranks"].items()} == {r.property_id: (r.rank, r.score) for r in d.ranked}
    assert all(not t["property_id"].startswith("ku:") for t in rec["top"])
    assert rec["priorities"] == ["travel", "cost", "area"]


def test_api_recommend_and_agent_give_same_ranking(monkeypatch):
    from app.main import app
    from app.routers import agent as agent_router

    llm = ScriptedLLM([X("recommend", **FULL)], ["상위 매물을 골랐어요."])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    cl = TestClient(app)
    api = cl.post("/recommend", json=direct_req().model_dump(mode="json")).json()
    chat = cl.post("/agent/chat", json={"session_id": "same-1", "message": "추천"}).json()
    assert [(t["property_id"], t["score"]) for t in chat["recommendation_results"]["top"]] == \
        [(r["property_id"], r["score"]) for r in api["ranked"][:3]]


# 2 ---------------------------------------------------------------------------

def test_missing_required_fields_asks_question():
    c = Chat([X("recommend", budget_manwon=70)])
    s = c.say("예산 70만 원으로 집 추천해 줘")
    assert s["status"] == "need_info"
    assert "학교" in s["pending_required_fields"] and any("우선순위" in f for f in s["pending_required_fields"])
    assert s.get("last_recommendation") is None
    assert "알려 주세요" in s["response"]


def test_multi_turn_fills_missing_fields_and_keeps_previous():
    c = Chat([X("recommend", **{k: v for k, v in FULL.items() if k not in ("department", "department_buildings")}),
              X("update", department_buildings=["공대"])])
    s1 = c.say("예산 70만 원으로 이대 주변 집 추천해 줘. 통학이 중요해")
    assert s1["pending_required_fields"] == ["수업을 주로 듣는 학과 건물"]
    s2 = c.say("공과대학 수업이 많아.")
    assert s2["status"] == "ok" and s2["current_profile"]["budget_manwon"] == 70
    assert s2["last_recommendation"]["top"][0]["property_id"] == direct().ranked[0].property_id


def test_department_preset_fills_buildings():
    # 이대 학과 → 건물 예시표에 있는 학과면 건물을 따로 묻지 않는다 (엔진이 예시표를 씀)
    c = Chat([X("recommend", **{**FULL, "department": "컴퓨터공학전공", "department_buildings": None})])
    s = c.say("컴공이야")
    assert s["status"] == "ok"
    assert s["last_recommendation"]["request"]["department_buildings"] is None


def test_single_priority_needs_second():
    c = Chat([X("recommend", **{**FULL, "priorities": ["area"]})])
    s = c.say("면적이 제일 중요해")
    assert s["status"] == "need_info" and s["current_profile"]["priorities"] == ["area"]
    assert any("2순위" in n for n in s["notes"])


# 3, 6 ------------------------------------------------------------------------

def test_budget_change_keeps_other_fields_then_revert():
    c = Chat([X("recommend", **FULL), X("update", budget_manwon=80), X("revert")])
    s1 = c.say("추천해 줘")
    s2 = c.say("예산을 80만 원으로 바꾸면?")
    p = s2["current_profile"]
    assert p["budget_manwon"] == 80 and p["department_buildings"] == ["공과대학"] and p["priorities"] == ["travel", "cost"]
    assert s2["comparison"]["total_after"] == len(direct(budget_manwon=80).ranked)
    assert s2["comparison"]["total_before"] == s1["last_recommendation"]["total_ranked"]
    s3 = c.say("원래 예산으로 돌아가 줘.")
    assert s3["current_profile"]["budget_manwon"] == 70
    assert s3["last_recommendation"]["ranks"] == s1["last_recommendation"]["ranks"]


# 4 ---------------------------------------------------------------------------

def test_university_change_clears_buildings_and_uses_that_university():
    c = TurnChat([X("recommend", **FULL), X("update", university="고려대"), X("update", department_buildings=["정경관"])])
    c.say("추천")
    c.say("고려대로 학교를 바꿔 줘")  # 이대 건물이 지워지므로 확인 질문
    s2 = c.say("네")
    assert s2["current_profile"]["university"] == KU and s2["current_profile"]["department_buildings"] == []
    assert s2["status"] == "need_info" and any("건물" in n for n in s2["notes"])
    s3 = c.say("정경관 수업이 많아")
    assert s3["status"] == "ok"
    assert all(pid.startswith("ku:") for pid in s3["last_recommendation"]["ranks"])


# 5 ---------------------------------------------------------------------------

def test_priority_change_reorders_weights():
    c = Chat([X("recommend", **FULL), X("update", priorities=["area", "travel"])])
    s1 = c.say("추천")
    assert s1["last_recommendation"]["weights"] == {"travel": pytest.approx(0.55), "cost": pytest.approx(0.30),
                                                    "area": pytest.approx(0.15)}
    s2 = c.say("면적이 제일 중요하고 그다음 통학")
    assert s2["current_profile"]["priorities"] == ["area", "travel"]
    assert s2["last_recommendation"]["weights"]["area"] == pytest.approx(0.55)
    assert s2["last_recommendation"]["ranks"] == {
        r.property_id: [r.rank, r.score] for r in direct(priorities=("area", "travel")).ranked}


def test_single_priority_moves_to_front():
    c = Chat([X("recommend", **FULL), X("update", priorities=["area"])])
    c.say("추천")
    s = c.say("면적을 1순위로")
    assert s["current_profile"]["priorities"] == ["area", "travel"]


# 7 ---------------------------------------------------------------------------

def test_what_if_does_not_overwrite_current_profile():
    c = Chat([X("recommend", **FULL), X("what_if", budget_delta_manwon=10)])
    s1 = c.say("추천")
    s2 = c.say("예산을 10만 원 올리면?")
    assert s2["current_profile"] == s1["current_profile"]  # 지금 조건 그대로
    assert s2["scenario_profile"]["budget_manwon"] == 80
    assert s2["comparison"]["label_after"] == "가정한 조건"
    assert s2["comparison"]["total_after"] == len(direct(budget_manwon=80).ranked)
    assert not s2["profile_changed"]


# 8 ---------------------------------------------------------------------------

def test_unknown_building_is_not_applied():
    c = Chat([X("recommend", **{**FULL, "department_buildings": ["없는관"]})])
    s = c.say("없는관 수업이 많아")
    assert s["current_profile"]["department_buildings"] == []
    assert s["status"] == "need_info"
    assert any("없는관" in n and "데이터에 없는 건물" in n for n in s["notes"])


# 9 ---------------------------------------------------------------------------

def test_listing_without_walk_time_is_excluded_with_reason():
    c = Chat([X("recommend", **{**FULL, "university": "고려대", "budget_manwon": 200, "department_buildings": ["신공학관"]})])
    s = c.say("고려대 신공학관")
    rec = s["last_recommendation"]
    assert "ku:P010" not in rec["ranks"] and rec["excluded_count"] >= 1
    assert rec["total_ranked"] == 9


# 10 --------------------------------------------------------------------------

def test_unsupported_request_is_reported_not_scored():
    c = Chat([X("recommend", unsupported=["알바 위치"], **FULL)])
    s = c.say("통학이랑 알바 위치도 반영해 줘")
    assert any("알바 위치" in n for n in s["notes"])
    assert s["last_recommendation"]["ranks"] == {r.property_id: [r.rank, r.score] for r in direct().ranked}


# 11 --------------------------------------------------------------------------

def test_llm_failure_returns_status_and_keeps_state():
    c = Chat([X("recommend", **FULL), LLMError("조건 추출 실패 (Timeout)")])
    s1 = c.say("추천")
    s2 = c.say("예산 바꿔 줘")
    assert s2["status"] == "llm_error" and "다시 시도" in s2["response"]
    assert s2["current_profile"] == s1["current_profile"]


def test_busy_error_message_is_specific():
    c = Chat([LLMError("조건 추출 실패 (busy)", "busy")])
    s = c.say("추천")
    assert s["status"] == "llm_error" and s["llm_error_kind"] == "busy" and "혼잡" in s["response"]
    assert "Gemini" not in s["response"]


# 분석 도구 (Pareto · Trade-off · 민감도 · 조건 완화 · 실거래 · 편의시설) -------------------

def _analysis_chat(kind, rank=None):
    return Chat([X("recommend", **FULL), X("analyze", analysis=kind, rank=rank)])


def test_analysis_pareto_matches_api_and_keeps_ranking():
    from app.decision.pareto import ParetoRequest, run_pareto

    c = _analysis_chat("pareto")
    s1 = c.say("추천")
    s2 = c.say("모든 면에서 손해 없는 매물만 보여 줘")
    assert s2["status"] == "ok" and s2["intent"] == "analyze"
    want = run_pareto(ParetoRequest(request=direct_req())).model_dump(mode="json")
    assert s2["analysis"]["kind"] == "pareto" and s2["analysis"]["result"] == want
    assert s2["analysis_facts"]["frontier_count"] == len(want["frontier"])
    assert s2["last_recommendation"]["ranks"] == s1["last_recommendation"]["ranks"]  # 순위는 그대로
    assert "참고 분석" in s2["response"]  # 설명이 없으면 사실 문장


def test_analysis_tradeoff_uses_asked_rank_as_reference():
    c = _analysis_chat("tradeoff", rank=2)
    s1 = c.say("추천")
    s2 = c.say("2위 기준으로 돈 더 내면 얼마나 가까워져?")
    assert s2["analysis"]["result"]["reference"]["property_id"] == s1["last_recommendation"]["top"][1]["property_id"]


def test_analysis_sensitivity_and_relaxation_run():
    c = Chat([X("recommend", **FULL), X("analyze", analysis="sensitivity"), X("analyze", analysis="relaxation")])
    c.say("추천")
    s = c.say("순위 믿을 만해?")
    assert s["analysis"]["result"]["baseline_top"] == [r.property_id for r in direct().ranked[:3]]
    s = c.say("뭘 양보하면 후보가 늘어?")
    assert s["analysis"]["kind"] == "relaxation" and "baseline_candidates" in s["analysis"]["result"]


def test_analysis_without_recommendation_runs_engine_first():
    c = Chat([X("analyze", analysis="pareto", **FULL)])
    s = c.say("이대 공대, 70만 원, 통학 1순위 돈 2순위, 15분, 18㎡ 조건으로 효율적인 후보만")
    assert s["status"] == "ok" and s["analysis"]["kind"] == "pareto" and s["last_recommendation"]["top"]


def test_analysis_needs_required_fields():
    c = Chat([X("analyze", analysis="tradeoff", budget_manwon=70)])
    s = c.say("돈 더 내면 얼마나 가까워져?")
    assert s["status"] == "need_info" and s.get("analysis") is None


def test_analysis_market_uses_ranked_listing(monkeypatch):
    import app.market as mk

    c = _analysis_chat("market", rank=1)
    s1 = c.say("추천")
    top1 = s1["last_recommendation"]["top"][0]["property_id"]
    p = data.get_property(top1)
    sgg, dong = mk.address_parts(p.address)
    txs = tuple(mk.Tx("sh", sgg, dong, p.area_m2, p.deposit_manwon, r, date(2026, 9, 1), "다가구", "2026-11-15T00:00:00+09:00")
                for r in range(40, 52))
    monkeypatch.setattr(mk, "load_transactions", lambda: txs)
    s2 = c.say("1위 월세 주변 시세랑 비교해 줘")
    a = s2["analysis"]
    assert a["kind"] == "market" and a["property_id"] == top1 and a["result"]["n"] == 12
    assert s2["analysis_facts"]["rent_median"] == a["result"]["rent_median"]
    assert "실거래" in s2["response"]


def test_analysis_amenities_and_bad_rank(monkeypatch):
    import app.amenities as am

    c = Chat([X("recommend", **FULL), X("analyze", analysis="amenities", rank=1), X("analyze", analysis="amenities", rank=9)])
    s1 = c.say("추천")
    p = data.get_property(s1["last_recommendation"]["top"][0]["property_id"])
    monkeypatch.setattr(am, "load_stores", lambda: (
        am.Store("convenience", "가게A", "주소A", p.latitude + 0.0009, p.longitude, "202609"),) if p.latitude else ())
    s2 = c.say("1위 근처 편의점 있어?")
    assert s2["analysis"]["kind"] == "amenities" and s2["analysis"]["property_id"] == p.property_id
    s3 = c.say("9위 근처는?")
    assert s3["analysis"]["result"] is None and "9위 매물이 없습니다" in s3["response"]


def test_analysis_kind_missing_asks_again():
    c = Chat([X("recommend", **FULL), X("analyze")])
    c.say("추천")
    s = c.say("분석해 줘")
    assert s.get("analysis") is None and any("어떤 분석" in n for n in s["notes"])


# LLM 재시도·대체 모델 (실제 호출 없음) ---------------------------------------

class FakeChat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)  # Exception 또는 반환값
        self.calls = 0

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


class Msg:
    def __init__(self, content):
        self.content = content


def gemini(chats, clock=None):
    from app.agent.llm import GeminiLLM

    sleeps = []
    llm = GeminiLLM("k", list(chats), chat_factory=lambda m: chats[m], sleep=sleeps.append,
                    **({"clock": clock} if clock else {}))
    return llm, sleeps


BUSY = RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")


def test_gemini_retries_busy_then_succeeds():
    a = FakeChat([BUSY, BUSY, Msg("설명")])
    llm, sleeps = gemini({"m1": a})
    assert llm.explain("q", {}) == "설명" and a.calls == 3 and sleeps == [1.5, 3.0]


def test_gemini_falls_back_to_next_model_when_busy_or_missing():
    a = FakeChat([BUSY, BUSY, BUSY])
    b = FakeChat([RuntimeError("404 NOT_FOUND model")])
    c = FakeChat([X("recommend", budget_manwon=70)])
    llm, _ = gemini({"m1": a, "m2": b, "m3": c})
    assert llm.extract("70만", {}).changes.budget_manwon == 70
    assert (a.calls, b.calls, c.calls) == (3, 1, 1) and llm.last_model == "m3"


def test_gemini_auth_error_fails_fast_with_kind():
    a = FakeChat([RuntimeError("403 PERMISSION_DENIED API key not valid")])
    b = FakeChat([Msg("x")])
    llm, sleeps = gemini({"m1": a, "m2": b})
    with pytest.raises(LLMError) as e:
        llm.explain("q", {})
    assert e.value.kind == "auth" and b.calls == 0 and sleeps == []


# 설명 숫자 검증 -----------------------------------------------------------------

def test_explain_failure_or_invented_number_falls_back_to_facts():
    c = Chat([X("recommend", **FULL), X("recommend")], ["TOP 1은 유지비 월 12345만 원입니다."])
    s1 = c.say("추천")  # 설명에 사실에 없는 숫자 → 사실 문장으로 대체
    assert "12345" not in s1["response"] and "유지비 월" in s1["response"]
    s2 = c.say("다시")  # 설명 실패 → 사실 문장
    assert s2["status"] == "ok" and s2["response"].startswith(EWHA)


def test_grounded_explanation_is_used():
    c = Chat([X("recommend", **FULL)], ["반영한 조건으로 상위 매물을 골랐어요."])
    assert c.say("추천")["response"] == "반영한 조건으로 상위 매물을 골랐어요."


def test_numbers_grounded():
    facts = {"top": [{"cost": 55.0, "values": "8:36 출발"}]}
    assert numbers_grounded("유지비 55만 원, 8:36 출발, 1위", facts)
    assert not numbers_grounded("유지비 56만 원", facts)


# API --------------------------------------------------------------------------

def test_api_agent_chat(monkeypatch):
    from app.main import app
    from app.routers import agent as agent_router

    llm = ScriptedLLM([X("recommend", **FULL)], ["상위 매물을 골랐어요."])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    res = TestClient(app).post("/agent/chat", json={"session_id": "api-1", "message": "추천해 줘"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["recommendation_results"]["top"][0]["rank"] == 1
    assert body["updated_profile"]["university"] == EWHA and body["profile_changed"] is True
    assert body["analysis_results"] is None


def test_api_agent_chat_analysis(monkeypatch):
    from app.main import app
    from app.routers import agent as agent_router

    llm = ScriptedLLM([X("analyze", analysis="pareto", **FULL)])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    body = TestClient(app).post("/agent/chat", json={"session_id": "api-an", "message": "효율적인 후보"}).json()
    assert body["status"] == "ok" and body["analysis_results"]["kind"] == "pareto"
    assert body["recommendation_results"] is None  # 분석 턴은 분석 결과를 보여 준다


def test_api_agent_chat_without_key(monkeypatch):
    from app.agent.llm import LLMUnavailable
    from app.main import app
    from app.routers import agent as agent_router

    def unavailable():
        raise LLMUnavailable("EXAONE_API_KEY 등을 BE/.env.local 에 설정해야 합니다.")

    monkeypatch.setattr(agent_router, "_provider_llm", unavailable)
    body = TestClient(app).post("/agent/chat", json={"session_id": "api-2", "message": "추천"}).json()
    assert body["status"] == "llm_unavailable" and "설정되지 않았습니다" in body["assistant_message"]
    # 기존 추천 API 는 그대로
    assert TestClient(app).get("/recommend/buildings").status_code == 200


def test_api_chat_uses_fe_profile(monkeypatch):
    from app.main import app
    from app.routers import agent as agent_router

    llm = ScriptedLLM([X("recommend")], ["상위 매물을 골랐어요."])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    prof = {"university": EWHA, "budget_manwon": 70, "department": "공대", "department_buildings": ["공과대학"],
            "priorities": ["travel", "cost"], "desired_commute_min": 15, "desired_area_m2": 18}
    body = TestClient(app).post("/agent/chat", json={"session_id": "api-fe", "message": "추천", "profile": prof}).json()
    assert body["status"] == "ok" and body["profile_changed"] is False
    assert body["recommendation_results"]["top"][0]["property_id"] == direct().ranked[0].property_id


# 추천 명세 · 턴 기록 ------------------------------------------------------------

def test_capability_spec_comes_from_code_and_data():
    from app.agent.spec import capability_spec
    from app.scoring_config import CONFIG

    spec = capability_spec(KU)
    assert "55%, 30%, 15%" in spec
    assert f"편도 {CONFIG.unit_travel_min:g}분 = 월 {CONFIG.unit_cost_manwon:g}만 원" in spec
    assert "pareto" in spec and "market" in spec
    assert "이화여자대학교 10건" in spec and "고려대학교 10건" in spec and "서강대학교" in spec  # 데이터에서 센 값
    assert "정경관" in spec  # 고른 대학의 건물


def test_spec_is_given_to_extract_and_explain():
    c = Chat([X("recommend", **FULL)], ["상위 매물을 골랐어요."])
    c.say("추천")
    assert "service_spec" in c.llm.contexts[0] and "[추천 방식" in c.llm.contexts[0]["service_spec"]
    assert "how_it_works" in c.llm.facts[0]


def test_spec_numbers_are_allowed_in_explanation():
    c = Chat([X("recommend", **FULL)], ["1순위 이동 시간 비중이 55%라 통학이 가까운 매물이 위에 있어요."])
    s = c.say("왜 1위야?")
    assert s["trace"]["explanation_source"] == "llm"


def test_trace_records_intent_tools_and_source():
    sink = []
    llm = ScriptedLLM([X("recommend", **FULL), X("what_if", budget_delta_manwon=10), X("analyze", analysis="pareto"),
                       LLMError("x", "busy")],
                      ["상위 매물을 골랐어요.", "TOP 1 유지비 월 99999만 원"])
    graph = build_graph(lambda: llm, trace_sink=sink.append)
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    for m in ("추천", "10만 원 올리면?", "효율적인 후보", "다시"):
        graph.invoke({"user_message": m, "session_id": "tr", "fe_profile": None}, config=cfg)
    t1, t2, t3, t4 = sink
    assert t1["intent"] == "recommend" and "recommendation" in t1["tools"] and t1["explanation_source"] == "llm"
    assert [n["node"] for n in t1["nodes"]][:2] == ["extract", "update_profile"] and t1["top"]
    assert t2["intent"] == "what_if" and "recommendation×2 + comparison" in t2["tools"]
    assert t2["explanation_source"] == "fallback_ungrounded" and t2["changes"] == {"budget_delta_manwon": 10}
    assert t3["intent"] == "analyze" and "recommendation + analysis" in t3["tools"] and t3["analysis"] == "pareto"
    assert t4["status"] == "llm_error" and t4["llm_error_kind"] == "busy" and t4["explanation_source"] == "fallback_error"
    assert t4["tools"] == [] and t4["session_id"] == "tr"


def test_api_returns_trace_and_writes_jsonl(monkeypatch, tmp_path):
    import json as _json

    from app.main import app
    from app.routers import agent as agent_router

    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("AGENT_TRACE_PATH", str(path))
    llm = ScriptedLLM([X("recommend", **FULL)], ["상위 매물을 골랐어요."])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    body = TestClient(app).post("/agent/chat", json={"session_id": "api-tr", "message": "추천"}).json()
    assert body["trace"]["intent"] == "recommend"
    line = _json.loads(path.read_text(encoding="utf-8").strip())
    assert line["session_id"] == "api-tr" and line["explanation_source"] == "llm"


# 확인 단계 (LangGraph interrupt) --------------------------------------------------

class TurnChat(Chat):
    """API 와 같은 경로(run_turn)로 대화한다: 확인 질문 중이면 답으로 이어 간다."""

    def __init__(self, extractions, explanations=None):
        super().__init__(extractions, explanations)
        self.sid = str(uuid.uuid4())

    def say(self, msg, fe_profile=None):
        from app.agent.graph import run_turn
        return run_turn(self.graph, self.sid, msg, fe_profile)


def test_university_change_asks_confirmation_then_applies_on_yes():
    from app.agent.graph import to_response_parts

    c = TurnChat([X("recommend", **FULL), X("update", university="고려대")])
    c.say("추천")
    s = c.say("학교를 고려대로 바꿔 줘")
    parts = to_response_parts(s)
    assert parts["status"] == "confirm" and "지워져요" in parts["assistant_message"]
    assert parts["pending_change"]["university"] == {"before": EWHA, "after": KU}
    assert parts["updated_profile"].university == EWHA and parts["profile_changed"] is False
    s2 = c.say("네")
    assert s2["current_profile"]["university"] == KU and s2["profile_changed"]
    assert s2["status"] == "need_info"  # 고려대 건물이 아직 없음


def test_confirmation_no_keeps_profile():
    c = TurnChat([X("recommend", **FULL), X("update", university="고려대")])
    s1 = c.say("추천")
    c.say("고려대로 바꿔")
    s = c.say("아니요")
    assert s["current_profile"] == s1["current_profile"] and s["confirm_declined"]
    assert "그대로" in s["response"]


def test_other_message_during_confirmation_cancels_and_is_processed():
    c = TurnChat([X("recommend", **FULL), X("update", university="고려대"), X("update", budget_manwon=80)])
    c.say("추천")
    c.say("고려대로 바꿔")
    s = c.say("예산 80으로 해 줘")  # 네/아니요가 아님 → 학교 변경 취소 후 새 요청 처리
    assert s["current_profile"]["university"] == EWHA and s["current_profile"]["budget_manwon"] == 80
    assert s["status"] == "ok"


def test_full_revert_with_several_fields_asks_confirmation():
    from app.agent.graph import to_response_parts

    c = TurnChat([X("recommend", **FULL), X("update", budget_manwon=80, deposit_budget_manwon=500), X("revert")])
    c.say("추천")
    c.say("예산 80, 보증금 500")
    parts = to_response_parts(c.say("전부 되돌려 줘"))
    assert parts["status"] == "confirm" and "되돌릴까요" in parts["assistant_message"]
    assert "월 유지비 상한(만원): 80 → 70" in parts["assistant_message"]
    s = c.say("응")
    assert s["current_profile"]["budget_manwon"] == 70 and s["current_profile"]["deposit_budget_manwon"] is None


def test_yes_no():
    from app.agent.graph import yes_no

    assert [yes_no(m) for m in ("네", "응 바꿔줘", "ㅇㅇ", "아니요", "ㄴㄴ", "그대로 둬", "예산 80으로", "어떻게 돼?")] == \
        [True, True, True, False, False, False, None, None]


# 시간표 사진 입력 ------------------------------------------------------------------

def test_timetable_image_endpoint_checks_slots(monkeypatch):
    from app.agent.schemas import SlotRead, TimetableRead
    from app.main import app
    from app.routers import agent as agent_router

    read = TimetableRead(slots=[
        SlotRead(day="월", start="09:00", end="10:15", building_text="공대", course="자료구조"),
        SlotRead(day="수", start="13:00", end="14:15", building_text="ECC B146"),
        SlotRead(day="목", start="10:30", end="11:45", building_text="우주과학관"),
        SlotRead(day="금", start="15:00", end="14:00", building_text="학관"),
    ], notes=["토요일 칸은 흐려서 못 읽음"])
    llm = ScriptedLLM([read])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    b = TestClient(app).post("/agent/timetable-image",
                             json={"university": EWHA, "image_base64": "aGVsbG8gd29ybGQ=", "mime_type": "image/png"}).json()
    assert b["status"] == "ok" and len(b["slots"]) == 4 and b["notes"] == ["토요일 칸은 흐려서 못 읽음"]
    s1, s2, s3, s4 = b["slots"]
    assert s1["building"] == "공과대학" and s1["issue"] is None and s1["course"] == "자료구조"
    assert s2["building"] == "ECC" and s2["issue"] is None  # 강의실 번호(B146)를 떼고 맞춤
    assert s3["building"] is None and "우주과학관" in s3["issue"]
    assert s4["issue"] == "종료 시각이 시작보다 빠름"
    assert "공과대학" in b["buildings"] and "확인이 필요" in b["message"]


def test_llm_explain_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_EXPLAIN", "off")
    c = Chat([X("recommend", **FULL)], ["이 설명은 쓰지 않는다"])
    s = c.say("추천")
    assert s["trace"]["explanation_source"] == "template" and s["response"].startswith(EWHA)
    assert c.llm.facts == []  # 설명용 LLM 호출 없음


def test_analysis_building(monkeypatch):
    import app.building_register as br

    c = Chat([X("recommend", **FULL), X("analyze", analysis="building", rank=2)])
    s1 = c.say("추천")
    p = data.get_property(s1["last_recommendation"]["top"][1]["property_id"])
    key = br.address_key(p.address)
    monkeypatch.setattr(br, "load_titles", lambda: {key: {
        "address": p.address, "match_status": "matched", "bld_nm": "테스트빌", "main_purps": "제2종근린생활시설",
        "use_apr_day": "20100101", "grnd_flr_cnt": "4", "elevator_cnt": "0", "parking_cnt": "2", "fetched_at": "t"}})
    s2 = c.say("2위 몇 년 된 건물이야? 엘리베이터 있어?")
    a = s2["analysis"]
    assert a["kind"] == "building" and a["property_id"] == p.property_id and a["result"]["use_approval_year"] == 2010
    assert "근린생활시설" in s2["response"] and "승강기 0대" in s2["response"]


# 제외 조건 (통합안 5장, 상가정보로 확인) ------------------------------------------

@pytest.fixture
def with_coords(monkeypatch, tmp_path):
    """이대 PoC 10건에 좌표를 붙인 데이터 + 가짜 상가정보 (P001·P002 근처에 주점, P003 근처에만 편의점)."""
    import app.amenities as am
    import app.place_search as ps

    rows = list(csv.DictReader(io.StringIO(data.DEFAULT_CSV_PATH.read_text(encoding="utf-8-sig"))))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    coords = {}
    for i, r in enumerate(rows):
        lat, lon = 37.5600 + i * 0.002, 126.9450
        coords[r["property_id"]] = (lat, lon)
        w.writerow(dict(r, latitude=lat, longitude=lon))
    f = tmp_path / "ewha_xy.csv"
    f.write_text(out.getvalue(), encoding="utf-8")
    monkeypatch.setenv("DATA_CSV_PATH", str(f))
    data.load_properties.cache_clear()
    stores = (
        am.Store("bar", "주점1", "a", coords["P001"][0] + 0.0003, 126.9450, "202609"),  # 약 33m
        am.Store("bar", "주점2", "b", coords["P002"][0] + 0.0005, 126.9450, "202609"),  # 약 56m
        am.Store("convenience", "편의점1", "c", coords["P003"][0] + 0.0005, 126.9450, "202609"),  # P003 약 56m, P004 약 167m
    )
    monkeypatch.setattr(ps, "load_stores", lambda: stores)
    monkeypatch.setenv("PLACE_SEARCH", "local")
    yield coords
    data.load_properties.cache_clear()


def P(place, want, **kw):
    from app.agent.schemas import PlacePref
    return [PlacePref(place=place, want=want, **kw)]


def test_place_condition_asks_plan_then_applies(with_coords):
    from app.agent.graph import to_response_parts

    c = TurnChat([X("recommend", **{**FULL, "budget_manwon": 200}), X("update", place_prefs=P("술집", "avoid"))])
    s1 = c.say("추천")
    assert {"P001", "P002"} <= set(s1["last_recommendation"]["ranks"])
    parts = to_response_parts(c.say("술집 근처는 싫어"))
    assert parts["status"] == "confirm" and "반경 100m 안에 술집(주점)이(가) 1곳 이상이면 제외" in parts["assistant_message"]
    assert parts["updated_profile"].exclusions == []  # 승인 전에는 저장 안 함
    s = c.say("네")
    ranks = s["last_recommendation"]["ranks"]
    assert "P001" not in ranks and "P002" not in ranks and s["last_recommendation"]["exclusion_counts"] == {"술집": 2}
    assert s["current_profile"]["exclusions"][0]["source_text"] == "술집"
    assert s["comparison"]["total_after"] == s1["last_recommendation"]["total_ranked"] - 2


def test_place_condition_matches_direct_engine_and_api(with_coords):
    from app.exclusions import ExclusionPlan
    from app.main import app
    from app.place_search import LocalStoreSearcher

    c = TurnChat([X("recommend", **{**FULL, "budget_manwon": 200}, place_prefs=P("편의점", "near", radius_m=100))])
    c.say("편의점이 100m 안에 있는 곳으로")
    s = c.say("네")
    plans = [ExclusionPlan.model_validate(e) for e in s["current_profile"]["exclusions"]]
    props = data.load_properties()
    d = recommend(props, direct_req(budget_manwon=200, exclusions=plans), travel=data.load_travel_times(props),
                  searcher=LocalStoreSearcher())
    assert {k: tuple(v) for k, v in s["last_recommendation"]["ranks"].items()} == {r.property_id: (r.rank, r.score) for r in d.ranked}
    assert set(s["last_recommendation"]["ranks"]) == {"P003"}
    api = TestClient(app).post("/recommend", json=direct_req(budget_manwon=200, exclusions=plans).model_dump(mode="json")).json()
    assert [r["property_id"] for r in api["ranked"]] == ["P003"]


def test_place_condition_declined_and_unsupported_and_remove(with_coords):
    c = TurnChat([X("recommend", **{**FULL, "budget_manwon": 200}), X("update", place_prefs=P("술집", "avoid")),
                  X("update", place_prefs=P("도서관", "near")), X("update", place_prefs=P("노래방", "avoid", max_count=2)),
                  X("update", remove_exclusions=["노래방"])])
    s1 = c.say("추천")
    c.say("술집 싫어")
    s = c.say("아니요")
    assert s["current_profile"]["exclusions"] == [] and s["current_profile"] == s1["current_profile"]
    s = c.say("도서관 가까이")
    assert s["current_profile"]["exclusions"] == [] and any("도서관" in n and "확인할 수 없어" in n for n in s["notes"])
    c.say("노래방 2곳까지는 괜찮아")
    s = c.say("네")
    assert s["current_profile"]["exclusions"][0]["conditions"][0]["exclude_if"] == {"op": ">", "value": 2.0}
    s = c.say("노래방 조건 빼 줘")  # 지우기는 확인 없이
    assert s["status"] == "ok" and s["current_profile"]["exclusions"] == []


def test_place_condition_what_if_does_not_save(with_coords):
    c = Chat([X("recommend", **{**FULL, "budget_manwon": 200}), X("what_if", place_prefs=P("술집", "avoid"))])
    s1 = c.say("추천")
    s2 = c.say("술집 근처를 빼면 어때?")
    assert s2["current_profile"] == s1["current_profile"] and s2["scenario_recommendation"]["exclusion_counts"] == {"술집": 2}
    assert s2["comparison"]["total_after"] == s2["comparison"]["total_before"] - 2


def test_local_store_searcher_paging_and_unknown():
    from app.amenities import Store
    from app.place_search import LocalStoreSearcher, local_category

    s = LocalStoreSearcher(tuple(Store("bar", f"b{i}", "", 37.5 + i * 0.0001, 127.0, "") for i in range(20)))
    p1 = s.search(lat=37.5, lng=127.0, radius_m=500, query="호프", category=None, page=1, size=15)
    p2 = s.search(lat=37.5, lng=127.0, radius_m=500, query="술집", category=None, page=2, size=15)
    assert len(p1["documents"]) == 15 and not p1["meta"]["is_end"] and len(p2["documents"]) == 5 and p2["meta"]["is_end"]
    assert p1["documents"][0]["distance"] == "0"
    assert local_category("CS2") == "convenience" and local_category("도서관") is None
    with pytest.raises(ValueError):
        s.search(lat=37.5, lng=127.0, radius_m=500, query="도서관", category=None, page=1, size=15)


# 평가 세트 · 채점기 (통합안 기준) ------------------------------------------------

def _gold():
    """케이스마다 '정답 추출'. 이 추출이면 모든 기대값을 통과해야 한다 (기대값이 서버 규칙으로 달성 가능한지 확인)."""
    from app.agent.schemas import SlotIn

    full = dict(university="이대", budget_manwon=70, department="공대", department_buildings=["공대"],
                priorities=["travel", "cost"], desired_commute_min=15, desired_area_m2=18)
    tt = [SlotIn(day="월", start="09:00", end="10:15", building="공대"), SlotIn(day="수", start="13:00", end="14:15", building="ECC")]
    return {
        "c01": X("recommend", **full), "c02": X("recommend", university="고대", budget_manwon=60),
        "c03": X("recommend", budget_manwon=75, deposit_budget_manwon=1000), "c04": X("recommend", budget_manwon=70),
        "c05": X("update", desired_area_m2=16.5), "c06": X("update", desired_commute_min=10),
        "c07": X("update", larger_area="like"), "c08": X("update", larger_area="dislike"),
        "c09": X("recommend", university="서강대", budget_manwon=65), "c10": X("update", department_buildings=["포관", "학관"]),
        "c11": X("update", priorities=["cost", "travel"]), "c12": X("update", priorities=["area"]),
        "c13": X("update", priorities=["area", "travel"]), "c14": X("update", budget_manwon=80),
        "c15": X("update", budget_delta_manwon=10), "c16": X("update", deposit_budget_manwon=500),
        "c17": X("what_if", budget_delta_manwon=10), "c18": X("what_if", desired_commute_min=20),
        "c19": X("revert"), "c20": X("update", budget_manwon=60), "c21": X("compare_previous"),
        "c22": X("explain", rank=2), "c23": X("explain"), "c24": X("analyze", analysis="market", rank=1),
        "c25": X("analyze", analysis="amenities", rank=3), "c26": X("analyze", analysis="building", rank=1),
        "c27": X("analyze", analysis="tradeoff"), "c28": X("analyze", analysis="pareto"),
        "c29": X("analyze", analysis="sensitivity"), "c30": X("analyze", analysis="relaxation"),
        "c31": X("update", place_prefs=P("술집", "avoid")), "c32": X("update", place_prefs=P("편의점", "near", radius_m=200)),
        "c33": X("update", place_prefs=P("노래방", "avoid", max_count=2)), "c34": X("update", place_prefs=P("도서관", "near")),
        "c35": X("update", remove_exclusions=["술집"]), "c36": X("what_if", place_prefs=P("술집", "avoid")),
        "c37": X("update", timetable=tt), "c38": X("smalltalk", unsupported=["알바 위치"]),
        "c39": X("smalltalk", unsupported=["심야 안전"]), "c40": X("smalltalk"),
        "c41": TurnExtraction(intent="policy", policy_query="월세 지원"),
        "c42": TurnExtraction(intent="policy", policy_query="전세 대출 이자"),
    }


def test_eval_cases_file_is_valid():
    from app.agent.evaluate import load_cases

    keys = {"intent", "profile", "scenario", "unchanged", "includes", "first_priority", "timetable_len",
            "property_rank", "analysis", "places", "place_rule", "no_places", "unsupported"}
    intents = {"recommend", "update", "what_if", "revert", "compare_previous", "analyze", "policy", "explain", "smalltalk"}
    cases = load_cases()
    assert len(cases) >= 40 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert set(c["expect"]) <= keys, c["id"]
        assert set(c["expect"].get("intent", [])) <= intents, c["id"]
        AgentProfile.model_validate(c["context"].get("current") or {})


def test_eval_gold_extractions_score_100_and_wrong_ones_fail():
    from app.agent.evaluate import load_cases, run

    cases = load_cases()
    gold = _gold()
    assert set(gold) == {c["id"] for c in cases}
    r = run(ScriptedLLM([gold[c["id"]] for c in cases]), cases)
    bad = [(x["id"], {k: v for k, v in x["checks"].items() if not v}, x.get("error")) for x in r["rows"] if not x["ok"]]
    assert r["case_accuracy"] == 1, bad
    wrong = {"c14": X("update", budget_manwon=75), "c17": X("update", budget_delta_manwon=10),
             "c31": X("update", place_prefs=P("술집", "near")), "c24": X("analyze", analysis="amenities", rank=1)}
    sub = [c for c in cases if c["id"] in wrong]
    r = run(ScriptedLLM([wrong[c["id"]] for c in sub] + [LLMError("x", "busy")]), sub + [cases[0]])
    assert r["case_accuracy"] == 0 and r["errors"] == 1
    c17 = next(x for x in r["rows"] if x["id"] == "c17")
    assert c17["checks"]["intent"] is False and c17["checks"]["scenario.current_kept"] is False


def test_top_groups_same_house_ads(monkeypatch, tmp_path):
    """같은 집일 수 있는 광고(주소·보증금·월세·관리비·면적 같음)는 TOP 에 하나만, 엔진 순위는 그대로."""
    rows = list(csv.DictReader(io.StringIO(data.DEFAULT_CSV_PATH.read_text(encoding="utf-8-sig"))))
    d = direct()
    first = next(r for r in rows if r["property_id"] == d.ranked[0].property_id)
    dup = dict(first, property_id="DUP1")  # 1위와 같은 집 광고 → 엔진에서는 1위와 같은 점수로 2위
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows + [dup])
    f = tmp_path / "dup.csv"
    f.write_text(out.getvalue(), encoding="utf-8")
    monkeypatch.setenv("DATA_CSV_PATH", str(f))
    data.load_properties.cache_clear()
    c = Chat([X("recommend", **FULL)])
    s = c.say("추천")
    rec = s["last_recommendation"]
    assert s["status"] == "ok"
    top_ids = [t["property_id"] for t in rec["top"]]
    assert len(top_ids) == 3 and not ({first["property_id"], "DUP1"} <= set(top_ids))
    assert rec["top"][0]["same_house_ids"] and [t["rank"] for t in rec["top"]][1] == 3  # 2위(같은 집)는 건너뜀
    assert "같은 집일 수 있는 광고 1건 더" in s["response"]


def test_explain_with_separate_provider(monkeypatch):
    import app.agent.graph as g
    import app.agent.llm as L

    explainer = ScriptedLLM([], ["상위 매물을 골랐어요 (설명 전용)."])
    monkeypatch.setenv("AGENT_LLM_EXPLAIN", "gemini")
    monkeypatch.setattr(L, "_FACTORIES", {"gemini": lambda: explainer, "exaone": lambda: None})
    monkeypatch.setattr(g, "_EXPLAINERS", {})
    c = Chat([X("recommend", **FULL)], ["추출 LLM 의 설명은 쓰지 않음"])
    s = c.say("추천")
    assert s["response"] == "상위 매물을 골랐어요 (설명 전용)." and c.llm.facts == [] and len(explainer.facts) == 1
    # 설명 공급자 실패 → 사실 문장
    monkeypatch.setattr(g, "_EXPLAINERS", {"gemini": ScriptedLLM([], [LLMError("x", "quota")])})
    s2 = Chat([X("recommend", **FULL)]).say("추천")
    assert s2["trace"]["explanation_source"] == "fallback_llm_failed" and s2["response"].startswith(EWHA)
