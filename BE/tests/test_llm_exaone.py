"""EXAONE (OpenAI 호환) LLM 연결 — 실제 API 는 부르지 않는다 (가짜 client).

확인: 구조화 출력 파싱(추론 태그·코드 블록), 스키마 모드, 재시도·대체 모델, 공급자 선택, 이미지 미지원 → OCR 경로.
"""

import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import (
    ExaoneLLM, GeminiLLM, LLMError, LLMUnavailable, exaone_from_env, llm_from_env, parse_json_object,
)
from app.agent.schemas import TurnExtraction


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


def make_llm(replies, models=("m1",), **kw):
    comp = FakeCompletions(replies)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    llm = ExaoneLLM("k", "https://example.invalid/v1", list(models), chat_factory=lambda m: (client, m),
                    sleep=lambda s: None, **kw)
    return llm, comp


# ---------------------------------------------------------------- 파싱

def test_parse_json_object_strips_thoughts_and_fences():
    assert parse_json_object('<thought>먼저 생각…{"x":0}</thought>\n```json\n{"intent": "recommend"}\n```') == {"intent": "recommend"}
    assert parse_json_object('답: {"a": {"b": 1}} 끝') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        parse_json_object("JSON 없음")


# ---------------------------------------------------------------- extract / explain

def test_extract_prompt_mode_puts_schema_in_system_and_validates():
    llm, comp = make_llm(['{"intent": "update", "changes": {"budget_manwon": 80}}'])
    out = llm.extract("예산 80으로", {"current_profile": {}})
    assert isinstance(out, TurnExtraction) and out.intent == "update" and out.changes.budget_manwon == 80
    call = comp.calls[0]
    assert "JSON 스키마" in call["messages"][0]["content"] and "response_format" not in call
    assert call["temperature"] == 0 and call["model"] == "m1" and llm.last_model == "m1"


def test_extract_json_schema_mode_uses_response_format_and_reasoning_effort():
    llm, comp = make_llm(['{"intent": "smalltalk"}'], json_mode="json_schema", reasoning_effort="low")
    assert llm.extract("안녕", {}).intent == "smalltalk"
    call = comp.calls[0]
    assert call["response_format"]["type"] == "json_schema" and call["response_format"]["json_schema"]["name"] == "TurnExtraction"
    assert call["extra_body"] == {"reasoning_effort": "low"} and "JSON 스키마" not in call["messages"][0]["content"]


def test_extract_bad_json_is_bad_response_without_retry():
    llm, comp = make_llm(["추천해 드릴게요!", '{"intent": "recommend"}'])
    with pytest.raises(LLMError) as e:
        llm.extract("추천", {})
    assert e.value.kind == "bad_response" and len(comp.calls) == 1


def test_explain_strips_reasoning():
    llm, _ = make_llm(["<think>숫자 확인</think>\n1위는 월 55만 원입니다."])
    assert llm.explain("왜?", {"x": 1}) == "1위는 월 55만 원입니다."


# ---------------------------------------------------------------- 재시도·대체 모델 (공통 규칙)

def test_busy_retries_then_falls_back_and_auth_fails_fast():
    # EXAONE 은 혼잡 시 같은 모델로 4번 더 (총 5번) 시도한 뒤 다음 모델로
    llm, comp = make_llm([Exception("Error code: 503 overloaded")] * 5 + ['{"intent": "recommend"}'], models=("a", "b"))
    assert llm.extract("추천", {}).intent == "recommend" and llm.last_model == "b"
    assert [c["model"] for c in comp.calls] == ["a"] * 5 + ["b"]
    llm2, _ = make_llm([Exception("Error code: 401 invalid api key")])
    with pytest.raises(LLMError) as e:
        llm2.extract("추천", {})
    assert e.value.kind == "auth" and "EXAONE" in str(e.value) and "k" not in str(e.value).split()


def test_exaone_concurrency_429_is_busy_but_real_quota_is_quota():
    from app.agent.llm import classify_error

    busy = Exception("Error code: 429 - {'error': {'message': 'service is busy, too many concurrent requests(600)', "
                     "'type': 'rate_limit_error', 'code': 'rate_limit_exceeded'}}")
    assert classify_error(busy) == "busy"
    assert classify_error(Exception("Error code: 429 - daily quota exceeded")) == "quota"
    llm, comp = make_llm([busy, '{"intent": "recommend"}'])
    assert llm.extract("추천", {}).intent == "recommend" and len(comp.calls) == 2  # 혼잡은 같은 모델로 다시 시도


def test_token_and_credit_errors_switch_provider():
    # 토큰·크레딧 소진 → quota (공급자를 자정까지 건너뜀), 입력 토큰 초과 → too_long (이번 호출만 전환). 2026-11-15 사용자 결정
    from app.agent.llm import SWITCH_KINDS, classify_error

    assert classify_error(Exception("Error code: 402 - insufficient credit")) == "quota"
    assert classify_error(Exception("daily token limit reached")) == "quota"
    assert classify_error(Exception("Error code: 400 - {'code': 'context_length_exceeded', "
                                    "'message': 'maximum context length is 32768 tokens'}")) == "too_long"
    assert classify_error(Exception("Error code: 400 - bad request")) == "other"  # 일반 400 은 전환 안 함
    assert {"quota", "too_long"} <= SWITCH_KINDS
    llm, comp = make_llm([Exception("maximum context length exceeded"), '{"intent": "recommend"}'], models=("a", "b"))
    assert llm.extract("추천", {}).intent == "recommend" and [c["model"] for c in comp.calls] == ["a", "b"]


def test_gemini_per_minute_429_is_busy_not_daily_quota():
    """Gemini 는 분당 한도도 429 → 분 단위 표시면 busy(5분 미룸), 하루 단위 표시면 quota(자정까지). 2026-11-16"""
    from app.agent.llm import classify_error

    rpm = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric: generate_content_free_tier_requests, "
           "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier. Please retry in 23.4s.")
    rpd = "429 RESOURCE_EXHAUSTED. quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier. Please retry in 5s."
    assert classify_error(Exception(rpm)) == "busy"
    assert classify_error(Exception(rpd)) == "quota"  # 하루 한도 표시가 있으면 재시도 문구가 있어도 quota
    assert classify_error(Exception("429 RESOURCE_EXHAUSTED")) == "quota"  # 표시가 없으면 지금처럼 하루 한도


def test_provider_chain_shares_one_time_budget():
    """공급자를 바꿔도 전체 대기 시간은 chain_budget_s 하나. 시간이 모자라면 다음 공급자를 시도하지 않는다."""
    from app.agent.llm import LLMError, ProviderChain

    t = [0.0]

    class Slow:
        def __init__(self, cost, kind=None):
            self.cost, self.kind, self.total_budget_s, self.seen, self.last_model = cost, kind, 45.0, [], "m"

        def extract(self, *a):
            self.seen.append(self.total_budget_s)
            t[0] += self.cost
            if self.kind:
                raise LLMError("x", self.kind)
            return "ok"

    g, e = Slow(30, "busy"), Slow(10)
    chain = ProviderChain([("gemini", g), ("exaone", e)], chain_budget_s=60, budget_clock=lambda: t[0])
    assert chain.extract("m", {}) == "ok" and g.seen == [45.0] and e.seen == [30.0]  # 남은 30초만 줌
    t[0] = 0.0
    g2, e2 = Slow(55, "busy"), Slow(10)
    chain2 = ProviderChain([("gemini", g2), ("exaone", e2)], chain_budget_s=60, budget_clock=lambda: t[0])
    with pytest.raises(LLMError):
        chain2.extract("m", {})
    assert e2.seen == []  # 5초 남음 → EXAONE 은 시도하지 않음
    assert chain.extract("m", {}) == "ok" and e.total_budget_s <= 45.0  # 원래 상한 기준으로 다시 계산


def test_read_timetable_not_supported():
    llm, _ = make_llm([])
    with pytest.raises(LLMUnavailable):
        llm.read_timetable("aGk=", "image/png", [])


# ---------------------------------------------------------------- 환경 변수 · 공급자 선택

def _env(monkeypatch, **values):
    import app.agent.llm as mod
    monkeypatch.setattr(mod, "_read", lambda name: values.get(name))


def test_exaone_from_env_requires_key_url_model(monkeypatch):
    _env(monkeypatch, EXAONE_API_KEY="k", EXAONE_MODEL="m")
    with pytest.raises(LLMUnavailable):
        exaone_from_env()
    _env(monkeypatch, EXAONE_API_KEY="k", EXAONE_BASE_URL="https://example.invalid/v1", EXAONE_MODEL="m",
         EXAONE_FALLBACK_MODELS="m2, m", EXAONE_JSON_MODE="json_schema", EXAONE_REASONING_EFFORT="low")
    llm = exaone_from_env()
    assert llm.models == ["m", "m2"] and llm.json_mode == "json_schema" and llm.reasoning_effort == "low"


def test_llm_provider_default_gemini_then_exaone(monkeypatch):
    """2026-11-16 결정: LLM_PROVIDER 없으면 Gemini 먼저, LLM_FALLBACK_PROVIDERS 없으면 EXAONE 이 대체."""
    from app.agent.llm import ProviderChain

    both = dict(GEMINI_API_KEY="k", GEMINI_MODEL="g",
                EXAONE_API_KEY="k", EXAONE_BASE_URL="https://example.invalid/v1", EXAONE_MODEL="m")
    _env(monkeypatch, **both)
    chain = llm_from_env()
    assert isinstance(chain, ProviderChain) and [n for n, _ in chain.providers] == ["gemini", "exaone"]
    _env(monkeypatch, EXAONE_API_KEY="k", EXAONE_BASE_URL="https://example.invalid/v1", EXAONE_MODEL="m")
    only = llm_from_env()  # Gemini 설정이 없으면 EXAONE 만 남은 체인
    assert [n for n, _ in only.providers] == ["exaone"] and isinstance(only.providers[0][1], ExaoneLLM)
    _env(monkeypatch, LLM_PROVIDER="gemini", LLM_FALLBACK_PROVIDERS="", GEMINI_API_KEY="k", GEMINI_MODEL="g")
    assert isinstance(llm_from_env(), GeminiLLM)  # 대체를 비워 두면 Gemini 하나만
    _env(monkeypatch, LLM_PROVIDER="foo")
    with pytest.raises(LLMUnavailable):
        llm_from_env()


# ---------------------------------------------------------------- 시간표 사진: 이미지 미지원 LLM → OCR

def test_timetable_image_falls_back_to_ocr_when_llm_has_no_vision(monkeypatch):
    from app import timetable_ocr
    from app.main import app
    from app.routers import agent as agent_router
    from app.timetable_ocr import OcrResult, OcrSlot

    llm, _ = make_llm([])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    monkeypatch.setattr(timetable_ocr, "read_timetable_image",
                        lambda data: OcrResult(slots=[OcrSlot("화", "11:00", "12:15", "학", "학109", "기독교와세계")]))
    b = TestClient(app).post("/agent/timetable-image", json={
        "university": "이화여자대학교", "image_base64": base64.b64encode(b"fake-image-bytes").decode(), "mime_type": "image/png"}).json()
    assert b["status"] == "ok" and b["slots"][0]["building"] == "학관"


def test_extract_accepts_null_changes_and_unsupported():
    # EXAONE 이 '바꿀 조건 없음'을 null 로 준 실제 응답 (2026-11-15 평가)
    llm, _ = make_llm(['\n\n{"intent": "analyze", "changes": null, "analysis": "pareto", "unsupported": null, "property_rank": null}'])
    out = llm.extract("모든 면에서 손해 없는 매물만", {})
    assert out.intent == "analyze" and out.analysis == "pareto" and out.unsupported == []
    assert out.changes.budget_manwon is None


# 공급자 전환 (ProviderChain: EXAONE → Gemini) ------------------------------------

class _P:
    """가짜 공급자: 정한 결과(값 또는 예외)를 차례로 돌려준다."""

    def __init__(self, name, outcomes):
        self.name, self.outcomes, self.calls, self.last_model = name, list(outcomes), 0, f"{name}-m"

    def explain(self, user_message, facts):
        self.calls += 1
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    def read_timetable(self, *a):
        from app.agent.llm import LLMUnavailable
        raise LLMUnavailable(f"{self.name} 이미지 없음")


def test_chain_quota_switches_and_skips_until_midnight():
    from app.agent.llm import LLMError, ProviderChain

    now = [1_700_000_000.0]
    ex = _P("exaone", [LLMError("한도", "quota"), "안 불려야 함"])
    ge = _P("gemini", ["g1", "g2"])
    chain = ProviderChain([("exaone", ex), ("gemini", ge)], clock=lambda: now[0])
    assert chain.explain("q", {}) == "g1" and chain.last_model == "gemini:gemini-m"
    assert chain.explain("q", {}) == "g2" and ex.calls == 1  # 오늘은 EXAONE 을 다시 부르지 않음
    now[0] += 2 * 86400  # 다음 날
    ex.outcomes = ["e-next-day"]
    assert chain.explain("q", {}) == "e-next-day" and chain.last_provider == "exaone"


def test_chain_busy_cools_down_then_returns_and_bad_response_does_not_switch():
    import pytest as _pt

    from app.agent.llm import LLMError, ProviderChain

    now = [1000.0]
    ex = _P("exaone", [LLMError("혼잡", "busy"), "e2", LLMError("형식", "bad_response")])
    ge = _P("gemini", ["g1", "g2", "unused"])
    chain = ProviderChain([("exaone", ex), ("gemini", ge)], clock=lambda: now[0], busy_cooldown_s=300)
    assert chain.explain("q", {}) == "g1"
    assert chain.explain("q", {}) == "g2" and ex.calls == 1  # 5분 동안은 EXAONE 을 기다리지 않음
    now[0] += 301
    assert chain.explain("q", {}) == "e2"  # 5분 뒤 다시 주 공급자
    with _pt.raises(LLMError) as e:
        chain.explain("q", {})
    assert e.value.kind == "bad_response" and ge.calls == 2


def test_chain_tries_cooled_provider_when_others_fail():
    from app.agent.llm import LLMError, ProviderChain

    now = [0.0]
    ex = _P("exaone", [LLMError("혼잡", "busy"), "e-ok"])
    ge = _P("gemini", [LLMError("혼잡", "busy"), LLMError("한도", "quota")])
    chain = ProviderChain([("exaone", ex), ("gemini", ge)], clock=lambda: now[0])
    try:
        chain.explain("q", {})
    except LLMError:
        pass
    now[0] += 10  # 둘 다 혼잡으로 미뤄진 상태 → 그래도 차례로 시도 (gemini 한도 → exaone 성공)
    assert chain.explain("q", {}) == "e-ok"


def test_chain_all_fail_and_timetable_uses_primary_only():
    import pytest as _pt

    from app.agent.llm import LLMError, LLMUnavailable, ProviderChain

    chain = ProviderChain([("exaone", _P("exaone", [LLMError("x", "busy")])), ("gemini", _P("gemini", [LLMError("y", "busy")]))])
    with _pt.raises(LLMError) as e:
        chain.explain("q", {})
    assert e.value.kind == "busy"
    with _pt.raises(LLMUnavailable) as e2:
        chain.read_timetable("b64", "image/png", [])
    assert "exaone" in str(e2.value)  # 이미지는 주 공급자에만 (EXAONE → OCR 경로)


def test_llm_from_env_builds_chain_and_skips_unconfigured(monkeypatch):
    import app.agent.llm as L

    env = {"LLM_PROVIDER": "exaone", "LLM_FALLBACK_PROVIDERS": "gemini"}
    monkeypatch.setattr(L, "_read", lambda k: env.get(k))
    made = {"exaone": lambda: "EX", "gemini": lambda: "GE"}
    monkeypatch.setattr(L, "_FACTORIES", made)
    chain = L.llm_from_env()
    assert isinstance(chain, L.ProviderChain) and [n for n, _ in chain.providers] == ["exaone", "gemini"]

    def no_key():
        raise L.LLMUnavailable("키 없음")
    monkeypatch.setattr(L, "_FACTORIES", {"exaone": no_key, "gemini": lambda: "GE"})
    assert [n for n, _ in L.llm_from_env().providers] == ["gemini"]
    env.pop("LLM_FALLBACK_PROVIDERS")
    monkeypatch.setattr(L, "_FACTORIES", made)
    assert L.llm_from_env() == "EX"  # 대체 공급자 없으면 그대로
