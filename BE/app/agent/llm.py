"""LLM 연결: Gemini (주 모델, langchain-google-genai), K-EXAONE 2.5 (OpenAI 호환 API, 대체), 테스트용 가짜 LLM.

- 키·모델은 서버 환경 변수만 쓴다 (BE/.env.local). 키는 로그·응답·예외에 넣지 않는다.
  - GEMINI_API_KEY, GEMINI_MODEL (+ GEMINI_FALLBACK_MODELS): 대화의 주 모델 (11/14 측정: 한 턴 약 4초)
  - EXAONE_API_KEY (+ EXAONE_MODEL, EXAONE_BASE_URL, EXAONE_REASONING_EFFORT): Gemini 가 안 될 때 조건 추출·계획만 대신
  - LLM_PROVIDER: gemini (기본) | exaone
- LLM 이 하는 일은 세 가지뿐이다 (프롬프트는 규칙 문서에서 만든다, app.agent.rules).
  1) extract: 사용자 메시지 → 의도 + 바꿀 조건 (TurnExtraction)
  2) plan:    기호 문장 → 제외 조건 계획 (PlanDraft). 서버가 검증한다
  3) explain: 서버가 계산한 사실(JSON) → 추천 요약 문장. 숫자는 사실에 있는 것만 (서버가 다시 확인)
- 점수·정렬·매물 선택·제외 판단은 하지 않는다 (코드).
- 시간표 사진은 읽지 않는다 (R-202, 11/15 결정).
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal, Protocol

from app.agent import rules
from app.agent.schemas import PlanDraft, TurnExtraction
from app.config import _read

TIMEOUT_S = 20          # 호출 한 번의 제한 시간
RETRIES = 2             # 같은 모델에서 다시 해 보는 횟수 (혼잡·시간 초과일 때만)
BACKOFF_S = 1.5         # 재시도 간격: 1.5초, 3초, …
TOTAL_BUDGET_S = 45     # 한 번의 호출이 쓰는 전체 시간 상한 (사용자가 기다리는 시간)

ErrorKind = Literal["busy", "quota", "auth", "not_found", "bad_response", "other"]


class LLMUnavailable(RuntimeError):
    """키나 모델이 설정되지 않음."""


class LLMError(RuntimeError):
    """호출 실패. kind 로 종류를 나눈다. 메시지에 키를 넣지 않는다."""

    def __init__(self, message: str, kind: ErrorKind = "other"):
        super().__init__(message)
        self.kind: ErrorKind = kind


def classify_error(e: BaseException) -> ErrorKind:
    """Gemini·OpenAI 호환(EXAONE) 예외 → 종류. 상태 코드·상태 이름 문자열로 판단한다 (SDK 예외 클래스가 버전마다 달라서)."""
    s = f"{type(e).__name__} {e}"
    if any(k in s for k in ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "high demand", "Timeout", "timed out",
                            "502", "InternalServerError", "APIConnectionError", "Connection error")):
        return "busy"
    if any(k in s for k in ("429", "RESOURCE_EXHAUSTED", "quota", "RateLimitError", "Too Many Requests")):
        return "quota"
    if any(k in s for k in ("401", "403", "PERMISSION_DENIED", "UNAUTHENTICATED", "API key", "API_KEY",
                            "AuthenticationError", "PermissionDeniedError")):
        return "auth"
    if any(k in s for k in ("404", "NOT_FOUND", "NotFoundError")):
        return "not_found"
    return "other"


class AgentLLM(Protocol):
    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction: ...
    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft: ...
    def explain(self, user_message: str, facts: dict[str, Any]) -> str: ...


# ---------------------------------------------------------------- 프롬프트 (규칙 문서에서 만든다)

def extract_system() -> str:
    unis = ", ".join(rules.universities())
    return f"""너는 대학생 자취방 추천 챗봇의 조건 추출기다. 사용자의 이번 메시지에서 의도와 '바꿀 조건'만 뽑아 JSON 으로 낸다.
계산·추천·설명은 하지 않는다. 아래 규칙 문서의 의도·입력 조건·되묻기·거절 규칙을 따른다.

추출 규칙
- 사용자가 이번 메시지에서 실제로 말한 값만 채운다. 말하지 않은 칸은 null. 추측하거나 기본값을 넣지 않는다 (R-603).
- 숫자는 말한 그대로 넣는다. 평 → ㎡ 환산, 더하기·빼기는 하지 않는다 (서버가 한다).
  · "6평" → area_value 6, area_unit "평" · "25제곱미터"·"25㎡" → area_value 25, area_unit "㎡"
  · "예산 10만 올려" → budget_delta_manwon 10 · "통학 5분 줄여" → commute_delta_min -5
- 금액 단위는 만 원. "월세랑 관리비 합쳐서 70" → budget_manwon 70. "보증금 1000" → deposit_budget_manwon 1000.
- 학교는 사용자 표현 그대로 또는 정식 이름 ({unis}). 지원하지 않는 학교도 말한 그대로 넣는다(서버가 거른다).
- 건물 이름은 사용자 표현 그대로 ("포관", "공대"). "~에서도 수업 들어"는 add_department_buildings. 학과 이름은 department.
- 우선순위 키: travel(이동 시간·통학), cost(예산·월세), area(면적·넓이).
  · 1·2순위를 둘 다 말하면 priorities [1순위, 2순위]. "통학보다 예산이 더 중요" → ["cost","travel"].
  · 1순위만 말하면 priority_first. 2순위만 말하면 priority_second. 비중 숫자("70%")는 순서로만 반영한다.
- 통학 시간을 버스·지하철 등 도보가 아닌 시간으로 말하면 commute_not_walking true, 숫자는 넣지 않는다 (R-307).
- 이미 있는 조건으로 표현되는 말은 조건 변경이다 (R-310): "보증금 500 넘으면 빼 줘" → deposit_budget_manwon 500,
  "20㎡보다 좁은 건 싫어" → area_value 20, "학교까지 15분 넘으면 싫어" → desired_commute_min 15. 이때 exclusion_request 는 null.
- 넓은 면적: "넓을수록 좋아" → larger_area like, "너무 넓으면 싫어" → dislike, "상관없어" → neutral.
- 수업 시간을 말하면 timetable_add 에 요일마다 한 칸씩 (HH:MM 24시간). 빠진 칸(강의동 등)은 null 로 둔다.
- 학년은 뽑지 않는다 (R-302).
- 기호(조용한 곳, 반지하 싫어, 편의점 가까웠으면 등)는 exclusion_request 에 그 부분 문장을 그대로 넣는다.
  조건 변경과 기호가 섞이면 changes 와 exclusion_request 를 둘 다 채운다.
- 지금 반영할 수 없는 요청(알바하는 곳·자주 가는 곳까지 거리 등)은 unsupported 에 짧게.

의도 (intent) — 규칙 문서 R-201
- update: 조건 입력·변경 ("예산 80으로 바꿔 줘", "포관 수업이 많아", "15분, 6평" 같은 짧은 답)
- recommend: 추천 요청 ("집 추천해 줘", "이 조건 그대로 다시 추천해 줘"). 조건이 같이 오면 changes 에도 담는다
- what_if: "~하면?", "~라면 어때?" — changes 에 가정한 변경을 담는다 (지금 조건은 바뀌지 않는다)
- revert: 이전 조건으로 전부 되돌리기. 일부만 되돌리면("원래 예산으로") update 로 하고 이전 조건의 값을 넣는다
- compare: 지금 결과와 이전 결과 비교
- explain: 결과·매물·추천 기준 설명 ("2등은 왜?" → property_rank 2, "어떤 기준으로 추천해?")
- exclude: 기호만 말함 → exclusion_request
- remove_exclusion: "술집 조건 빼 줘" → remove_exclusion "술집", "제외 조건 다 지워" → "all"
- out_of_scope: 범위 밖 (7장) → scope_topic: contract_legal(계약·특약·보증금 반환·분쟁), fraud_registry(사기·안전·등기),
  landlord(집주인·관리 상태), listing_guarantee(매물이 진짜 있는지·지금 계약 가능한지), market_forecast(시세 전망·투자),
  unsupported_region(지원하지 않는 지역·학교), unrelated(주거와 무관, 서버·키 정보 요청 포함),
  timetable_photo(시간표 사진·캡처를 읽어 달라는 요청)
- greeting: 인사 ("안녕", "안녕하세요")

맥락: current_profile(지금 조건), previous_profile(이전 조건), last_question(앞 턴에서 물은 칸, R-604),
exclusions(지금 제외 조건 이름), last_top(지난 추천 상위 매물), recent_messages.

{rules.rules_block(1, 2, 3, 6, 7)}
"""


def plan_system() -> str:
    return f"""너는 자취방 추천 챗봇의 '제외 조건 계획' 담당이다. 사용자의 기호 문장을 확인 도구 조건으로 바꿔 JSON 으로 낸다.
매물을 직접 판단하지 않는다. 어떤 도구로 무엇을 확인할지만 정한다. 확인은 코드가 한다 (R-501).

계획 규칙
- 조건은 '이 조건이면 매물을 뺀다'로 쓴다 (op, value / values). "~가 좋겠어/있었으면"은 반대 조건을 제외로 바꾼다 (R-502).
  · "편의점 가까웠으면" → amenity convenience nearest_m, op ">", value 200 (200m 넘으면 제외)
- 사용자가 숫자를 말하면 그 숫자를 쓰고, 말하지 않으면 context.catalog.defaults 의 기본값을 쓴다 (R-508).
- 도구·칸·시설·값은 context.catalog 에 있는 것만 쓴다. 종류 칸은 op in / not_in 과 values(고를 수 있는 값).
  · op in = values 에 있는 값이면 뺀다. op not_in = values 에 없는 값이면 뺀다 (= values 만 남긴다).
  · "A 싫어"·"A 빼 줘" → op in, values [A]. "A만"·"A만 볼래"·"A여야 해" → op not_in, values [A] (A 를 남길 값으로 쓴다).
    예: "분리형 원룸만" → room_type, op not_in, values ["분리형원룸"]. "투룸은 빼 줘" → room_type, op in, values ["투룸"].
- amenity 에 있는 시설은 amenity 를 쓰고, 없는 장소만 nearby_count·nearest_distance(keywords)를 쓴다.
- 방음·곰팡이·결로·채광·수압·배수·벌레·외풍·냄새·옵션 상태는 조건을 만들지 말고 onsite_checks 에 항목 이름을 넣는다.
  다른 데이터(건물 연식·층·구조)로 대신 판단하지 않는다 (R-503).
- 데이터로 확인할 수 없는 것(큰 도로 소음, 치안, 오르막 등)은 unsupported 에 짧게 적고 조건을 만들지 않는다 (R-504).
- 예산·보증금·면적·통학 시간으로 표현되는 말은 제외 조건을 만들지 않는다 (R-310, 조건 변경).
- 같은 대상(예: 편의점 거리)을 다시 말하면 새 기준으로 조건 하나만 만든다 (R-510). 지금 조건은 context.exclusions.

{rules.rules_block(5)}
"""


def explain_system() -> str:
    return f"""너는 자취방 추천 결과를 설명하는 도우미다. 아래 '사실' JSON 만 근거로 한국어 존댓말("~해요")로 짧게 쓴다.

규칙
- 사실에 없는 매물, 가격, 시간, 면적, 점수, 개수를 만들지 않는다. 숫자는 사실에 있는 값을 그대로 쓴다.
  빼기·평균·환산을 하지 않는다. 기준 대비 값은 top[].vs_target 문장을 그대로 쓴다 (R-801·802).
- 점수는 사용자가 물을 때만 말한다 (R-803). 순위를 바꾸지 않는다.
- 쓰는 것: 추천 요약(상위 매물의 주소·유형·보증금과 vs_target), 비교가 있으면 바뀐 점, 질문(mode=explain)에 대한 답.
  "2등은 왜?"에는 그 매물이 1위보다 약한 요소와 강한 요소를 vs_target·not_recommended 로 설명한다 (R-903).
- 쓰지 않는 것: '바뀐 것', 예산 초과 표시, 광고 매물 안내, 현장 확인 목록 — 코드가 따로 붙인다. 인사말도 생략한다.
- 안전하다·계약해도 된다처럼 보장하는 말을 쓰지 않는다 (R-702). 대중교통 시간은 말하지 않는다.
- 6문장 안팎. 목록은 매물 비교에만 쓴다.

{rules.rules_block(4, 8, 9)}
"""


def _limit_headers(e: BaseException) -> dict[str, str]:
    """429 등 응답의 한도 관련 헤더만 (진단·재시도 대기용). 키·본문은 넣지 않는다."""
    headers = getattr(getattr(e, "response", None), "headers", None) or {}
    try:
        return {k.lower(): v for k, v in headers.items() if "rate-limit" in k.lower() or k.lower().startswith("retry-after")}
    except Exception:  # noqa: BLE001
        return {}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def _human(context: dict[str, Any], label: str, text: str) -> str:
    return "맥락:\n" + json.dumps(context, ensure_ascii=False) + f"\n\n{label}:\n" + text


# ---------------------------------------------------------------- Gemini

def _default_chat_factory(api_key: str) -> Callable[[str], Any]:
    def make(model: str) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0,
                                      timeout=TIMEOUT_S, max_retries=0)
    return make


class GeminiLLM:
    """모델 목록(주 모델 + 대체 모델)을 순서대로 쓴다.

    - 혼잡·시간 초과(busy): 같은 모델로 RETRIES 번까지 간격을 늘려 다시 하고, 안 되면 다음 모델로.
    - 모델 없음(not_found)·사용 한도(quota, 429): 바로 다음 모델로 (한도는 모델별).
    - 인증·응답 형식 등: 다시 해도 같으므로 바로 실패.
    - 전체 시간이 TOTAL_BUDGET_S 를 넘으면 멈춘다.
    """

    provider = "Gemini"
    total_budget_s: float = TOTAL_BUDGET_S

    def __init__(self, api_key: str, models: list[str] | str, *,
                 chat_factory: Callable[[str], Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.models = [models] if isinstance(models, str) else list(models)
        if not self.models:
            raise LLMUnavailable(f"{self.provider} 모델이 비어 있습니다.")
        self._factory = chat_factory or self._default_factory(api_key)
        self._chats: dict[str, Any] = {}
        self._sleep, self._clock = sleep, clock
        self.last_model: str | None = None
        self.attempts: list[dict[str, Any]] = []
        self.calls = 0  # 실제로 보낸 요청 수 (평가 실행기의 한도 관리용)

    @property
    def model_name(self) -> str:
        return self.models[0]

    def _default_factory(self, api_key: str) -> Callable[[str], Any]:
        return _default_chat_factory(api_key)

    def _classify(self, e: BaseException) -> ErrorKind:
        return classify_error(e)

    def _backoff(self, attempt: int, e: BaseException) -> float:
        return BACKOFF_S * 2 ** attempt

    def _chat(self, model: str) -> Any:
        if model not in self._chats:
            self._chats[model] = self._factory(model)
        return self._chats[model]

    def _call(self, what: str, fn: Callable[[Any], Any]) -> Any:
        start = self._clock()
        last: ErrorKind = "other"
        self.attempts = []
        for model in self.models:
            for attempt in range(RETRIES + 1):
                if self._clock() - start > self.total_budget_s:
                    raise LLMError(f"{self.provider} {what} 실패: 시간 초과", "busy")
                t0 = time.monotonic()
                self.calls += 1
                try:
                    out = fn(self._chat(model))
                    self.last_model = model
                    self.attempts.append({"model": model, "result": "ok", "s": round(time.monotonic() - t0, 1)})
                    return out
                except LLMError as e:
                    self.attempts.append({"model": model, "result": e.kind, "s": round(time.monotonic() - t0, 1)})
                    raise
                except Exception as e:  # noqa: BLE001 — 외부 호출 실패. 키가 들어갈 수 있는 원문 대신 종류만 남긴다
                    last = self._classify(e)
                    self.attempts.append({"model": model, "result": last, "error": type(e).__name__,
                                          "status": getattr(e, "status_code", None),
                                          "s": round(time.monotonic() - t0, 1), **_limit_headers(e)})
                    if last == "busy" and attempt < RETRIES:
                        self._sleep(self._backoff(attempt, e))
                        continue
                    if last in ("busy", "not_found", "quota"):
                        break
                    raise LLMError(f"{self.provider} {what} 실패 ({last})", last) from e
        raise LLMError(f"{self.provider} {what} 실패 ({last}, 모델 {len(self.models)}개 모두)", last)

    def _structured(self, system: str, human: str, model_cls: type) -> Callable[[Any], Any]:
        prompt = [("system", system), ("human", human)]

        def run(chat: Any) -> Any:
            out = chat.with_structured_output(model_cls).invoke(prompt)
            if isinstance(out, model_cls):
                return out
            try:
                return model_cls.model_validate(out)
            except Exception as e:  # noqa: BLE001
                raise LLMError(f"{self.provider} 응답 형식이 올바르지 않습니다", "bad_response") from e
        return run

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        return self._call("조건 추출", self._structured(extract_system(), _human(context, "이번 메시지", message), TurnExtraction))

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        return self._call("제외 계획", self._structured(plan_system(), _human(context, "사용자 기호", request), PlanDraft))

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        prompt = [("system", explain_system()),
                  ("human", "사용자 메시지:\n" + user_message + "\n\n사실:\n" + json.dumps(facts, ensure_ascii=False))]
        return self._call("설명 생성", lambda chat: _content_text(chat.invoke(prompt).content).strip())


def gemini_from_env() -> GeminiLLM:
    """GEMINI_MODEL (주 모델) + GEMINI_FALLBACK_MODELS (쉼표, 선택: 혼잡할 때 차례로 쓸 모델)."""
    key, model = _read("GEMINI_API_KEY"), (_read("GEMINI_MODEL") or "").strip()
    if not key or not model:
        raise LLMUnavailable("GEMINI_API_KEY 와 GEMINI_MODEL 을 BE/.env.local 에 설정해야 합니다.")
    fallbacks = [m.strip() for m in (_read("GEMINI_FALLBACK_MODELS") or "").split(",") if m.strip()]
    return GeminiLLM(key, list(dict.fromkeys([model, *fallbacks])))


# ---------------------------------------------------------------- K-EXAONE 2.5 (LG AI연구원, OpenAI 호환 API)
#
# 문서: https://docs.exaone.ai/ko/docs/k-exaone-2.5 (2026-11-14 확인)
# - 추론 모델: 답은 message.content. reasoning_effort low | high | max. tools·response_format 없음 → JSON 지시 + 검증.
# - 2026-11-14 측정: 조건 추출 49~100초, 설명 67초~시간 초과 → 대화의 주 모델은 Gemini, EXAONE 은 추출·계획만 대신한다.

EXAONE_DEFAULT_BASE_URL = "https://api.exaone.ai/v1"
EXAONE_DEFAULT_MODEL = "k-exaone_v2.5"
EXAONE_TIMEOUT_S = 100
EXAONE_TOTAL_BUDGET_S = 110
EXAONE_MAX_TOKENS = 4096


def _json_instruction(model_cls: type) -> str:
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False)
    return ("\n출력 형식\n- 아래 JSON 스키마를 따르는 JSON 객체 하나만 출력한다. 설명 문장·마크다운·코드 블록 표시(```)를 붙이지 않는다.\n"
            "- 값이 없는 항목은 null 로 두거나 생략한다.\n스키마:\n" + schema)


def _parse_json_obj(text: str, model_cls: type) -> Any:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("JSON 객체가 없음")
    return model_cls.model_validate(json.loads(t[i:j + 1]))


class _OpenAICompatChat:
    """OpenAI 호환 /chat/completions 한 모델. complete(messages) → 답 문자열 (추론 내용은 버린다)."""

    def __init__(self, client: Any, model: str, reasoning_effort: str | None, max_tokens: int):
        self.client, self.model = client, model
        self.reasoning_effort, self.max_tokens = reasoning_effort, max_tokens
        self.last_limits: dict[str, str] = {}
        self.last_usage: dict[str, Any] = {}

    def complete(self, messages: list[dict[str, str]]) -> str:
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else None
        kw = dict(model=self.model, messages=messages, max_tokens=self.max_tokens, stream=False, extra_body=extra)
        t0 = time.monotonic()
        comp = self.client.chat.completions
        raw = getattr(comp, "with_raw_response", None)
        if raw is not None:
            r = raw.create(**kw)
            self.last_limits = _limit_headers(SimpleNamespace(response=r))
            res = r.parse()
        else:
            res = comp.create(**kw)
        choice = res.choices[0]
        text = (choice.message.content or "").strip()
        u = getattr(res, "usage", None)
        details = getattr(u, "completion_tokens_details", None)
        self.last_usage = {
            "s": round(time.monotonic() - t0, 1), "finish": choice.finish_reason,
            "prompt_tokens": getattr(u, "prompt_tokens", None), "completion_tokens": getattr(u, "completion_tokens", None),
            "reasoning_tokens": getattr(u, "reasoning_tokens", None) or getattr(details, "reasoning_tokens", None),
            "reasoning_chars": len(getattr(choice.message, "reasoning_content", None) or ""),
        }
        if not text:
            why = "추론이 길어 max_tokens 에서 잘림" if choice.finish_reason == "length" else "빈 답"
            raise LLMError(f"EXAONE 응답 없음 ({why})", "bad_response")
        return text


class ExaoneLLM(GeminiLLM):
    """K-EXAONE 2.5. 재시도·대체 모델·시간 상한 규칙은 GeminiLLM 과 같다 (_call).

    - 429 는 초·분 단위 한도일 때가 많아 '혼잡'처럼 잠깐 뒤 다시 한다.
    - JSON 이 깨지면 같은 호출 안에서 한 번 더 요청한다 (그래도 깨지면 bad_response).
    """

    provider = "EXAONE"
    total_budget_s: float = EXAONE_TOTAL_BUDGET_S

    def __init__(self, api_key: str, models: list[str] | str = EXAONE_DEFAULT_MODEL, *,
                 base_url: str = EXAONE_DEFAULT_BASE_URL, reasoning_effort: str | None = "low",
                 timeout_s: float = EXAONE_TIMEOUT_S, max_tokens: int = EXAONE_MAX_TOKENS, **kw: Any):
        self._base_url, self._reasoning_effort = base_url, reasoning_effort
        self._timeout_s, self._max_tokens = timeout_s, max_tokens
        super().__init__(api_key, models, **kw)

    def _default_factory(self, api_key: str) -> Callable[[str], Any]:
        def make(model: str) -> Any:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self._base_url, timeout=self._timeout_s, max_retries=0)
            return _OpenAICompatChat(client, model, self._reasoning_effort, self._max_tokens)
        return make

    def _classify(self, e: BaseException) -> ErrorKind:
        kind = classify_error(e)
        return "busy" if kind == "quota" else kind

    def _backoff(self, attempt: int, e: BaseException) -> float:
        h = _limit_headers(e)
        try:
            if "retry-after-ms" in h:
                return min(10.0, float(h["retry-after-ms"]) / 1000)
            if "retry-after" in h:
                return min(10.0, float(h["retry-after"]))
        except ValueError:
            pass
        return super()._backoff(attempt, e)

    def _structured(self, system: str, human: str, model_cls: type) -> Callable[[Any], Any]:
        msgs = [{"role": "system", "content": system + _json_instruction(model_cls)},
                {"role": "user", "content": human}]

        def run(chat: Any) -> Any:
            text = chat.complete(msgs)
            try:
                return _parse_json_obj(text, model_cls)
            except Exception:  # noqa: BLE001 — 한 번만 다시 요청
                retry = [*msgs, {"role": "assistant", "content": text[:2000]},
                         {"role": "user", "content": "형식이 맞지 않습니다. 스키마에 맞는 JSON 객체 하나만 다시 출력하세요."}]
                text2 = chat.complete(retry)
                try:
                    return _parse_json_obj(text2, model_cls)
                except Exception as e:  # noqa: BLE001
                    raise LLMError("EXAONE 응답 형식이 올바르지 않습니다", "bad_response") from e
        return run

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        msgs = [{"role": "system", "content": explain_system()},
                {"role": "user", "content": "사용자 메시지:\n" + user_message + "\n\n사실:\n" + json.dumps(facts, ensure_ascii=False)}]
        return self._call("설명 생성", lambda chat: chat.complete(msgs))


def exaone_from_env() -> ExaoneLLM:
    key = _read("EXAONE_API_KEY")
    if not key:
        raise LLMUnavailable("EXAONE_API_KEY 를 BE/.env.local 에 설정해야 합니다.")
    model = (_read("EXAONE_MODEL") or "").strip() or EXAONE_DEFAULT_MODEL
    base_url = (_read("EXAONE_BASE_URL") or "").strip() or EXAONE_DEFAULT_BASE_URL
    effort = (_read("EXAONE_REASONING_EFFORT") or "").strip() or "low"
    return ExaoneLLM(key, [model], base_url=base_url, reasoning_effort=effort)


class ChainLLM:
    """조건 추출·계획은 text 목록을 차례로, 설명은 explain 목록을 차례로 쓴다.

    앞 모델이 실패하면(키 없음 제외 모든 LLMError) 다음 제공자로 넘어간다. 마지막 제공자의 오류는 그대로 올린다.
    """

    def __init__(self, text: list[Any], explain: list[Any] | None = None):
        if not text:
            raise LLMUnavailable("대화용 LLM 이 없습니다.")
        self.text = list(text)
        self.explain_llms = list(explain) if explain is not None else self.text
        self.last_model: str | None = None

    @property
    def model_name(self) -> str:
        first = self.text[0]
        return f"{getattr(first, 'provider', '?')}:{getattr(first, 'model_name', '?')}"

    @property
    def calls(self) -> int:
        return sum(getattr(x, "calls", 0) for x in dict.fromkeys([*self.text, *self.explain_llms]))

    def _run(self, llms: list[Any], name: str, *args: Any) -> Any:
        for i, llm in enumerate(llms):
            try:
                out = getattr(llm, name)(*args)
                self.last_model = f"{getattr(llm, 'provider', '?')}:{getattr(llm, 'last_model', None)}"
                return out
            except LLMError:
                if i == len(llms) - 1:
                    raise
        raise AssertionError("unreachable")

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        return self._run(self.text, "extract", message, context)

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        return self._run(self.text, "plan", request, context)

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        return self._run(self.explain_llms, "explain", user_message, facts)


def llm_from_env() -> AgentLLM:
    """LLM_PROVIDER = 대화의 주 모델: gemini (기본) | exaone. 다른 쪽 키도 있으면 실패할 때 대체 모델로 쓴다.

    gemini 가 주 모델이면 EXAONE 은 조건 추출·계획만 대신한다 (설명은 1분 넘게 걸려서 넘기지 않고,
    Gemini 설명이 실패하면 그래프가 계산된 사실로 만든 문장을 바로 보여 준다).
    """
    try:
        gemini: GeminiLLM | None = gemini_from_env()
    except LLMUnavailable:
        gemini = None
    has_exaone = bool(_read("EXAONE_API_KEY"))
    provider = (_read("LLM_PROVIDER") or "").strip().lower() or ("exaone" if has_exaone and gemini is None else "gemini")
    if provider not in ("gemini", "exaone"):
        raise LLMUnavailable(f"LLM_PROVIDER 는 exaone 또는 gemini 여야 합니다 (지금: {provider}).")
    if provider == "gemini":
        if gemini is None:
            return gemini_from_env()
        if not has_exaone:
            return gemini
        return ChainLLM([gemini, exaone_from_env()], explain=[gemini])
    exaone = exaone_from_env()
    return ChainLLM([exaone, *([gemini] if gemini else [])])


# ---------------------------------------------------------------- 테스트용

class ScriptedLLM:
    """테스트용: 미리 정한 결과를 순서대로 돌려준다. explain 은 정한 문장(없으면 실패 → 그래프가 사실 문장으로 대신)."""

    def __init__(self, extractions: list[TurnExtraction | Exception], explanations: list[str | Exception] | None = None,
                 plans: list[PlanDraft | Exception] | None = None):
        self._ex = deque(extractions)
        self._exp = deque(explanations or [])
        self._plans = deque(plans or [])
        self.contexts: list[dict[str, Any]] = []
        self.plan_contexts: list[dict[str, Any]] = []
        self.facts: list[dict[str, Any]] = []
        self.calls = 0

    @staticmethod
    def _pop(q: deque, what: str) -> Any:
        if not q:
            raise LLMError(f"{what} 없음 (테스트)")
        x = q.popleft()
        if isinstance(x, Exception):
            raise x
        return x

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        self.calls += 1
        self.contexts.append(context)
        return self._pop(self._ex, "추출")

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        self.calls += 1
        self.plan_contexts.append(context)
        return self._pop(self._plans, "계획")

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        self.calls += 1
        self.facts.append(facts)
        return self._pop(self._exp, "설명")
