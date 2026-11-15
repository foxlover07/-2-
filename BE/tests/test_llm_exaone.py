"""K-EXAONE 2.5 연결 (실제 호출 없음 — 가짜 chat/client 로 확인).

- JSON 지시 + 서버 검증으로 구조화 출력, 깨지면 한 번 더 요청
- 429·혼잡은 잠깐 뒤 재시도, 인증 오류는 바로 실패
- ChainLLM: EXAONE 실패 시 Gemini 대체, 시간표 사진은 Gemini
- llm_from_env: LLM_PROVIDER / 키 유무에 따른 선택
"""

from types import SimpleNamespace

import pytest

from app.agent import llm as llm_mod
from app.agent.llm import (ChainLLM, ExaoneLLM, LLMError, LLMUnavailable, ScriptedLLM, _OpenAICompatChat,
                           _parse_json_obj)
from app.agent.schemas import ProfileChanges, TurnExtraction

GOOD = '{"intent": "recommend", "changes": {"budget_manwon": 70}, "unsupported": [], "property_rank": null}'


class FakeComplete:
    """ExaoneLLM 이 쓰는 chat: complete(messages) → 문자열."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.messages = []

    def complete(self, messages):
        self.calls += 1
        self.messages.append(messages)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def exaone(chats):
    sleeps = []
    llm = ExaoneLLM("k", list(chats), chat_factory=lambda m: chats[m], sleep=sleeps.append)
    return llm, sleeps


def test_extract_parses_json_even_with_code_fence_and_newlines():
    a = FakeComplete(["\n\n```json\n" + GOOD + "\n```"])
    llm, _ = exaone({"k-exaone_v2.5": a})
    out = llm.extract("70만 원 이하", {})
    assert out.intent == "recommend" and out.changes.budget_manwon == 70 and a.calls == 1
    sys_prompt = a.messages[0][0]["content"]
    assert "JSON 객체 하나만" in sys_prompt and '"intent"' in sys_prompt  # 스키마를 프롬프트로 준다
    assert llm.last_model == "k-exaone_v2.5"


def test_extract_asks_again_once_when_json_is_broken():
    a = FakeComplete(["조건을 정리하면 예산 70만 원입니다.", GOOD])
    llm, _ = exaone({"m": a})
    assert llm.extract("70만", {}).changes.budget_manwon == 70
    assert a.calls == 2 and a.messages[1][-1]["role"] == "user" and "JSON" in a.messages[1][-1]["content"]


def test_extract_broken_twice_is_bad_response():
    a = FakeComplete(["모르겠어요", '{"intent": "날씨"}'])
    llm, _ = exaone({"m": a})
    with pytest.raises(LLMError) as e:
        llm.extract("?", {})
    assert e.value.kind == "bad_response" and a.calls == 2


def test_rate_limit_429_is_retried_like_busy():
    a = FakeComplete([RuntimeError("Error code: 429 - Too Many Requests"), "설명입니다"])
    llm, sleeps = exaone({"m": a})
    assert llm.explain("q", {}) == "설명입니다" and sleeps == [1.5]


def test_openai_sdk_style_errors_are_classified():
    class APIConnectionError(Exception):
        pass

    class AuthenticationError(Exception):
        pass

    assert llm_mod.classify_error(APIConnectionError("Connection error.")) == "busy"
    assert llm_mod.classify_error(AuthenticationError("Error code: 401 - invalid key")) == "auth"
    assert llm_mod.classify_error(RuntimeError("Error code: 404 - model not found")) == "not_found"


def test_auth_error_fails_fast():
    a = FakeComplete([RuntimeError("Error code: 401 - Unauthorized")])
    llm, sleeps = exaone({"m": a})
    with pytest.raises(LLMError) as e:
        llm.explain("q", {})
    assert e.value.kind == "auth" and sleeps == [] and "EXAONE" in str(e.value)



# OpenAI 호환 호출 모양 ------------------------------------------------------------

class FakeClient:
    def __init__(self, content, finish="stop"):
        self.kwargs = None
        self.content, self.finish = content, finish
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.kwargs = kw
        msg = SimpleNamespace(content=self.content, reasoning_content="생각...")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=self.finish)])


def test_openai_compat_chat_sends_model_effort_and_drops_reasoning():
    c = FakeClient("\n\n답")
    out = _OpenAICompatChat(c, "k-exaone_v2.5", "low", 4096).complete([{"role": "user", "content": "hi"}])
    assert out == "답"  # 앞 공백 제거, reasoning_content 는 버림
    assert c.kwargs["model"] == "k-exaone_v2.5" and c.kwargs["max_tokens"] == 4096
    assert c.kwargs["extra_body"] == {"reasoning_effort": "low"} and c.kwargs["stream"] is False


def test_openai_compat_chat_empty_answer_after_length_is_bad_response():
    with pytest.raises(LLMError) as e:
        _OpenAICompatChat(FakeClient("", "length"), "m", None, 10).complete([])
    assert e.value.kind == "bad_response" and "max_tokens" in str(e.value)


def test_parse_json_obj_takes_outer_object():
    out = _parse_json_obj('결과: {"intent": "greeting", "changes": {}} 끝', TurnExtraction)
    assert out.intent == "greeting"


# ChainLLM ------------------------------------------------------------------------

def X(intent, **changes):
    return TurnExtraction(intent=intent, changes=ProfileChanges(**changes))


def test_chain_falls_back_to_gemini_when_exaone_fails():
    a = FakeComplete([RuntimeError("Error code: 401")])
    ex, _ = exaone({"m": a})
    gem = ScriptedLLM([X("recommend", budget_manwon=60)])
    gem.provider = "Gemini"
    chain = ChainLLM([ex, gem])
    assert chain.extract("60만", {}).changes.budget_manwon == 60
    assert chain.last_model.startswith("Gemini")


def test_chain_uses_exaone_first_and_reports_last_error():
    ex, _ = exaone({"m": FakeComplete([GOOD])})
    chain = ChainLLM([ex])
    assert chain.extract("70만", {}).intent == "recommend" and chain.last_model == "EXAONE:m"
    ex2, _ = exaone({"m": FakeComplete([RuntimeError("Error code: 401")])})
    with pytest.raises(LLMError) as e:
        ChainLLM([ex2]).explain("q", {})
    assert e.value.kind == "auth"



# 환경 변수로 고르기 ------------------------------------------------------------------

def env(monkeypatch, **values):
    monkeypatch.setattr(llm_mod, "_read", lambda name: values.get(name))  # .env.local 을 읽지 않게


def test_from_env_both_keys_defaults_to_gemini_first(monkeypatch):
    env(monkeypatch, EXAONE_API_KEY="e", GEMINI_API_KEY="g", GEMINI_MODEL="gemini-x")
    llm = llm_mod.llm_from_env()
    assert isinstance(llm, ChainLLM) and [t.provider for t in llm.text] == ["Gemini", "EXAONE"]
    assert llm.explain_llms == [llm.text[0]]  # 설명은 EXAONE 으로 넘기지 않음


def test_chain_explain_list_skips_slow_model():
    gem = ScriptedLLM([], [LLMError("busy", "busy")])
    gem.provider = "Gemini"
    ex, _ = exaone({"m": FakeComplete(["느린 설명"])})
    chain = ChainLLM([gem, ex], explain=[gem])
    with pytest.raises(LLMError):
        chain.explain("q", {})
    assert ex._chats == {}  # EXAONE 은 부르지 않음


def test_from_env_exaone_with_gemini_fallback(monkeypatch):
    env(monkeypatch, LLM_PROVIDER="exaone", EXAONE_API_KEY="e", GEMINI_API_KEY="g", GEMINI_MODEL="gemini-x")
    llm = llm_mod.llm_from_env()
    assert isinstance(llm, ChainLLM) and [t.provider for t in llm.text] == ["EXAONE", "Gemini"]
    assert llm.text[0].models == ["k-exaone_v2.5"]
    assert llm.text[0]._reasoning_effort == "low" and llm.text[0]._base_url == "https://api.exaone.ai/v1"


def test_from_env_exaone_only(monkeypatch):
    env(monkeypatch, EXAONE_API_KEY="e", EXAONE_MODEL="k-exaone_v2.5-x", EXAONE_REASONING_EFFORT="high")
    llm = llm_mod.llm_from_env()
    assert [t.provider for t in llm.text] == ["EXAONE"]
    assert llm.text[0].models == ["k-exaone_v2.5-x"] and llm.text[0]._reasoning_effort == "high"


def test_from_env_defaults_to_gemini_without_exaone_key(monkeypatch):
    env(monkeypatch, GEMINI_API_KEY="g", GEMINI_MODEL="gemini-x")
    assert type(llm_mod.llm_from_env()).__name__ == "GeminiLLM"
    with pytest.raises(LLMUnavailable, match="GEMINI"):
        env(monkeypatch, LLM_PROVIDER="gemini", EXAONE_API_KEY="e")
        llm_mod.llm_from_env()


def test_from_env_errors(monkeypatch):
    env(monkeypatch, LLM_PROVIDER="exaone")
    with pytest.raises(LLMUnavailable, match="EXAONE_API_KEY"):
        llm_mod.llm_from_env()
    env(monkeypatch, LLM_PROVIDER="claude")
    with pytest.raises(LLMUnavailable, match="exaone 또는 gemini"):
        llm_mod.llm_from_env()


def test_rate_limit_waits_retry_after_header():
    class RateLimitError(Exception):
        status_code = 429
        response = SimpleNamespace(headers={"Retry-After": "4", "x-minute-rate-limit-remaining": "0", "authorization": "x"})

    a = FakeComplete([RateLimitError("Error code: 429"), "답"])
    llm, sleeps = exaone({"m": a})
    assert llm.explain("q", {}) == "답" and sleeps == [4.0]
    assert llm.attempts[0]["retry-after"] == "4" and "authorization" not in llm.attempts[0]


def test_success_response_limit_headers_are_kept():
    class Raw:
        headers = {"x-day-rate-limit": "1000", "x-day-rate-limit-remaining": "990", "set-cookie": "x"}

        def parse(self):
            msg = SimpleNamespace(content="답", reasoning_content="")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=None)

    c = FakeClient("unused")
    c.chat.completions.with_raw_response = SimpleNamespace(create=lambda **kw: Raw())
    chat = _OpenAICompatChat(c, "m", "low", 100)
    assert chat.complete([]) == "답"
    assert chat.last_limits == {"x-day-rate-limit": "1000", "x-day-rate-limit-remaining": "990"}
