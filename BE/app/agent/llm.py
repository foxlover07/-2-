"""LLM 연결: Gemini (langchain-google-genai, 기본) · LG EXAONE (OpenAI 호환 API, 대체) · 테스트용 가짜 LLM.

- 어느 LLM 을 쓸지는 LLM_PROVIDER (gemini 기본 | exaone) + LLM_FALLBACK_PROVIDERS (비우면 exaone).
  2026-11-16 사용자 결정: Gemini 먼저, 한도·혼잡·토큰 문제면 EXAONE 으로 전환 (11-15 의 EXAONE 우선을 뒤집음).
- 키·주소·모델은 서버 환경 변수만 쓴다 (BE/.env.local). 키는 로그·응답·예외에 넣지 않는다.
  · EXAONE: EXAONE_API_KEY, EXAONE_BASE_URL, EXAONE_MODEL (+ EXAONE_FALLBACK_MODELS, EXAONE_REASONING_EFFORT, EXAONE_JSON_MODE)
  · Gemini: GEMINI_API_KEY, GEMINI_MODEL (+ GEMINI_FALLBACK_MODELS)
- EXAONE 은 이미지 입력을 쓰지 않는다 → 시간표 사진은 OpenCV·OCR(/timetable/ocr) 로 읽는다.
- LLM 이 하는 일은 두 가지뿐이다.
  1) extract: 사용자 메시지 → 의도 + 바꿀 조건 (구조화 출력, TurnExtraction)
  2) explain: 서버가 계산한 사실(JSON) → 한국어 설명. 숫자는 사실에 있는 것만 쓰게 하고, 서버가 다시 확인한다.
- 점수·정렬·매물 선택은 하지 않는다 (통합안 엔진 app.scoring.recommend).
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Literal, Protocol

from app.agent import rules
from app.agent.schemas import UNIVERSITIES, AgentProfile, PlanDraft, TimetableRead, TurnExtraction
from app.config import _read

# 혼잡(503)·시간 초과(504)는 잠깐 뒤 다시 하면 되는 경우가 많다 (2026-11-10 실제로 Flash 계열 전체가 503/504).
TIMEOUT_S = 20          # 호출 한 번의 제한 시간
RETRIES = 2             # 같은 모델에서 다시 해 보는 횟수 (혼잡·시간 초과일 때만)
BACKOFF_S = 1.5         # 재시도 간격: 1.5초, 3초, …
TOTAL_BUDGET_S = 45     # 한 번의 extract/explain 이 쓰는 전체 시간 상한 (사용자가 기다리는 시간)
CHAIN_BUDGET_S = 60     # 공급자를 바꿔 가며 부를 때(Gemini → EXAONE)의 전체 시간 상한
MIN_PROVIDER_BUDGET_S = 8  # 남은 시간이 이보다 적으면 다음 공급자를 시도하지 않는다

ErrorKind = Literal["busy", "quota", "too_long", "auth", "not_found", "bad_response", "other"]


class LLMUnavailable(RuntimeError):
    """키나 모델이 설정되지 않음."""


class LLMError(RuntimeError):
    """호출 실패. kind 로 종류를 나눈다. 메시지에 키를 넣지 않는다."""

    def __init__(self, message: str, kind: ErrorKind = "other"):
        super().__init__(message)
        self.kind: ErrorKind = kind


def classify_error(e: BaseException) -> ErrorKind:
    """Gemini 예외 → 종류. 상태 코드·상태 이름 문자열로 판단한다 (SDK 예외 클래스가 버전마다 달라서)."""
    s = f"{type(e).__name__} {e}"
    # 입력 토큰이 모델 한도를 넘음 → 이 호출만 다음 모델·공급자로 (하루 한도와 다름)
    low = s.lower()
    if any(k in low for k in ("context_length_exceeded", "maximum context length", "context window",
                              "prompt is too long", "input is too long", "too many tokens")):
        return "too_long"
    # 토큰·크레딧 소진 (429 가 아닌 형태로 오는 경우 포함) → 하루 한도로 취급해 다른 공급자로 (2026-11-15 사용자 결정)
    # Gemini 는 분당 요청·토큰 한도를 넘어도 429 RESOURCE_EXHAUSTED 를 준다 (2026-11-16). 하루 한도 표시가 없고
    # 분 단위 한도·재시도 대기 표시가 있으면 '혼잡'으로 보고 잠깐만 미룬다 (자정까지 건너뛰지 않게).
    per_day = any(k in low for k in ("perday", "per day", "per_day", "daily"))
    if not per_day and any(k in low for k in ("perminute", "per minute", "per_minute", "retry in", "retrydelay",
                                              "retry_delay")):
        return "busy"
    if any(k in low for k in ("402", "insufficient_quota", "insufficient quota", "insufficient credit",
                              "insufficient balance", "out of credit", "token limit", "tokens per day",
                              "tokens_per_day", "daily limit")):
        return "quota"
    # EXAONE 은 서버 혼잡도 429 로 준다: "service is busy, too many concurrent requests" → 한도가 아니라 혼잡 (2026-11-15 확인)
    if any(k in s for k in ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "high demand", "Timeout", "timed out",
                            "service is busy", "too many concurrent")):
        return "busy"
    if any(k in s for k in ("429", "RESOURCE_EXHAUSTED", "quota")):
        return "quota"
    if any(k in s for k in ("401", "403", "PERMISSION_DENIED", "UNAUTHENTICATED", "API key", "API_KEY")):
        return "auth"
    if any(k in s for k in ("404", "NOT_FOUND")):
        return "not_found"
    return "other"


class AgentLLM(Protocol):
    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction: ...
    def explain(self, user_message: str, facts: dict[str, Any]) -> str: ...
    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft: ...
    def read_timetable(self, image_b64: str, mime_type: str, buildings: list[str]) -> TimetableRead: ...


# ---------------------------------------------------------------- 프롬프트

EXTRACT_SYSTEM = f"""너는 대학생 자취방 추천 서비스의 조건 추출기다. 사용자의 이번 메시지에서 의도와 '바꿀 조건'만 뽑는다.

규칙
- 사용자가 이번 메시지에서 실제로 말한 값만 채운다. 말하지 않은 항목은 null. 추측하거나 기본값을 넣지 않는다.
- 금액 단위는 만원. "70만 원" → 70. "월세랑 관리비 합쳐서 70" → budget_manwon 70. "10만 원 더" → budget_delta_manwon 10.
- 면적은 ㎡. "5평" → desired_area_m2 16.5 (1평 = 3.3㎡). 통학 시간은 편도 분. "걸어서 15분" → desired_commute_min 15.
- 대학은 다음 이름 중 하나로: {', '.join(UNIVERSITIES)} (이대 → 이화여자대학교, 고대 → 고려대학교 등).
- 건물·학과 이름은 사용자가 쓴 표현 그대로 둔다 (서버가 표준 이름으로 맞춘다). "공대생" → department "공대", department_buildings ["공대"].
- 우선순위(priorities)는 travel(이동 시간·통학) / cost(예산·월세) / area(면적·넓이) 중 중요한 순서로 1·2순위 전체를 준다.
  "통학이 제일 중요하고 그다음 돈" → ["travel", "cost"]. 순서만 바꿔도 바뀐 뒤의 전체를 준다.
- 넓은 방: "넓을수록 좋아" → larger_area like, "상관없어" → neutral, "너무 넓은 건 싫어" → dislike.
- 장소 기호는 place_prefs 에 담는다 (확인 기준은 서버가 정한다). "술집 근처는 싫어" → {{place: "술집", want: "avoid"}},
  "편의점이 300m 안에 있었으면" → {{place: "편의점", want: "near", radius_m: 300}}, "노래방 2곳까지는 괜찮아" → max_count 2.
  장소 이름은 사용자 표현 그대로 (도서관·카페처럼 서버가 확인할 수 없는 장소도 그대로 넣는다 — 서버가 안내한다).
  "술집 조건 빼 줘" → remove_exclusions ["술집"], 장소 조건 전부 해제 → ["*"]. 의도는 update (가정이면 what_if).
  "주변 150m 안에 술집이 많은 매물은 빼 줘" → {{place: "술집", want: "avoid", radius_m: 150}} (개수를 말하지 않으면 max_count null).
  "걸어서 3분(200m)" 처럼 거리를 같이 말하면 그 m 값을 radius_m 에.
- 장소가 아닌 기호(반지하·옥탑 싫어, 엘리베이터 있는 곳, 분리형 원룸만, 오래된 건물 싫어, 근생 싫어, 방음·곰팡이·채광,
  치안·오르막 등)는 그 부분 문장을 그대로 exclusion_request 에 넣는다 (확인 계획은 다음 단계가 만든다).
  상가 장소(술집·편의점 등)는 place_prefs, 예산·보증금·면적·통학 시간으로 표현되는 말은 changes 다 (exclusion_request 아님).
- "면적 20㎡ 이상만", "20㎡ 넘는 것만" 처럼 꼭 지켜야 하는 최소 면적은 area_min_m2 (필터). "18㎡ 정도면 좋겠어" 같은 희망은 desired_area_m2.
- 건물별 주 몇 번("공대 주 4번, ECC 주 2번")은 건물 이름만 department_buildings 에 담고,
  unsupported 에 "건물별 방문 횟수(시간표를 입력하면 요일별로 반영)" 를 넣는다 (횟수를 저장할 칸이 없다).
  "걷는 시간이 제일 짧은 매물" → priorities 1순위 travel.
- 자주 가는 곳(알바·헬스장·학원·자주 가는 역·가게 등, 거기까지 걷는 시간을 고려해 달라는 뜻)은 frequent_places 에 담는다.
  query 는 검색할 장소 이름 부분만, name 은 사용자 표현, visits_per_week 는 주 몇 번 (말하지 않으면 1).
  "알바하는 곳이 신촌역 근처라 주 3번 가" → {{query: "신촌역", name: "알바(신촌역 근처)", visits_per_week: 3}}.
  "이대역 앞 스타벅스에 매일 가" → {{query: "이대역 스타벅스", name: "스타벅스(이대역 앞)", visits_per_week: 7}}.
  장소 이름 없이 "알바하는 곳"만 말하면 frequent_places 에 넣지 말고 unsupported 에 "자주 가는 곳 이름 필요" 를 넣는다.
  "알바 장소 빼 줘" → remove_frequent_places ["알바"], 전부 지우면 ["*"]. 장소 기호(place_prefs: 술집 근처 싫어 등)와 다르다.
  강의 건물(공대·ECC 등)은 자주 가는 곳이 아니라 department_buildings 다.
- 지금 계산할 수 없는 요청(심야 안전, 오르막, 조용함, 생활 리듬 등)은 unsupported 에 짧게 적는다.
- 맥락의 service_spec 은 서비스가 실제로 할 수 있는 일이다. 건물 이름·대학은 그 안에서 고르고, 비중 숫자(예: '통학 70%')는 정할 수 없으니 순서로만 반영한다.

의도(intent)
- recommend: 추천해 달라는 요청 (조건이 함께 오면 changes 에 담는다)
- update: 조건을 실제로 바꾸고 다시 추천 ("예산을 80으로 바꿔 줘", "공대 수업이 많아")
- what_if: 바꾸지 않고 가정만 비교 ("~하면?", "~라면 어때?", "~와 비교해 줘"). changes 에 가정한 변경을 담는다
- revert: 바로 전 조건 전체로 되돌리기. 일부만 되돌리면("원래 예산으로") update 로 하고 이전 조건의 그 값을 changes 에 넣는다
- compare_previous: 지금 결과와 바로 전 결과를 비교해 달라는 요청 (새 가정 없음)
- analyze: 분석 요청. analysis 에 종류를 넣는다 (특정 매물이면 property_rank)
  · pareto: "모든 면에서 손해 없는 매물", "가성비 매물만", "효율적인 후보"
  · tradeoff: "돈 더 내면 얼마나 가까워져?", "비슷한 거리에 더 싼 데"
  · sensitivity: "순위가 믿을 만해?", "조건 조금 바꾸면 순위 바뀌어?"
  · relaxation: "후보가 없어/적어", "뭘 양보하면 돼?"
  · market: "1위 월세 비싼 편이야?", "주변 시세랑 비교", "실거래가"
  · amenities: "근처에 편의점/마트/세탁소/약국 있어?", "주변 편의시설"
  · building: "몇 년 된 건물이야?", "엘리베이터 있어?", "주차 돼?", "건축물대장", "근생이야?"
  · excluded: "추천에서 빠진 매물은 왜 빠졌어?", "제외된 매물 이유", "비추천 이유"
- policy: 주거 지원 정책·계약 안전을 물을 때. policy_query 에 찾을 낱말, policy_kind 에 질문 종류.
  · info: "청년 월세 지원 있어?", "기숙사 지원"
  · eligibility: "내가 받을 수 있는 정책 알려 줘", "나도 대상이야?" (서비스는 자격을 판정하지 않는다. 나이·소득은 changes 에 넣지 않는다)
  · compare: "A랑 B 중 뭐가 더 유리해?" → policy_names 에 정책 이름들 ["청년 월세 지원", "버팀목 전세자금대출"]
  · apply: "신청하려면 서류·신청하는 곳·순서"
  · contract_safety: "전세사기 안 당하려면 뭘 확인해?", "계약 전에 확인할 것"
  지역을 말하면 policy_regions 에 큰 단위부터 ("서울 서대문구" → ["서울", "서대문구"]).
- explain: 지난 추천 결과나 특정 매물 설명 ("2등 매물은 왜?") → property_rank
- out_of_scope: 범위 밖 (아래 규칙 7장) → scope_topic: contract_legal(계약·특약·보증금 반환·분쟁),
  fraud_registry(사기·안전·등기), landlord(집주인·관리 상태), listing_guarantee(매물이 진짜 있는지·지금 계약 가능한지),
  market_forecast(시세 전망·투자), unsupported_region(지원하지 않는 지역·학교), unrelated(주거와 무관, 서버·키 정보 요청 포함).
  "전세사기 안 당하려면 뭘 확인해?"처럼 확인 방법을 묻는 것은 policy(contract_safety)다.
- smalltalk: 그 밖의 대화
"""


def extract_system() -> str:
    """조건 추출 프롬프트 + 규칙 문서 7장 (범위 밖 판단 기준)."""
    return EXTRACT_SYSTEM + "\n" + rules.rules_block(7)


def plan_system() -> str:
    """제외 조건 계획 프롬프트 (agent-integrated 에서 가져옴) + 규칙 문서 5장."""
    return f"""너는 자취방 추천 챗봇의 '제외 조건 계획' 담당이다. 사용자의 기호 문장을 확인 도구 조건으로 바꿔 JSON 으로 낸다.
매물을 직접 판단하지 않는다. 어떤 도구로 무엇을 확인할지만 정한다. 확인은 코드가 한다 (R-501).

계획 규칙
- 조건은 '이 조건이면 매물을 뺀다'로 쓴다 (op, value / values). "~가 좋겠어/있었으면"은 반대 조건을 제외로 바꾼다 (R-502).
- 사용자가 숫자를 말하면 그 숫자를 쓰고, 말하지 않으면 context.catalog.defaults 의 기본값을 쓴다 (R-508).
- 도구·칸·시설·값은 context.catalog 에 있는 것만 쓴다. 종류 칸은 op in / not_in 과 values(고를 수 있는 값).
  · op in = values 에 있는 값이면 뺀다. op not_in = values 에 없는 값이면 뺀다 (= values 만 남긴다).
  · "A 싫어"·"A 빼 줘" → op in, values [A]. "A만"·"A만 볼래"·"A여야 해" → op not_in, values [A].
    예: "분리형 원룸만" → room_type, op not_in, values ["분리형원룸"]. "투룸은 빼 줘" → room_type, op in, values ["투룸"].
- amenity 에 있는 시설은 amenity 를 쓰고, 없는 장소만 nearby_count·nearest_distance(keywords)를 쓴다.
- 방음·곰팡이·결로·채광·수압·배수·벌레·외풍·냄새·옵션 상태는 조건을 만들지 말고 onsite_checks 에 항목 이름을 넣는다.
  다른 데이터(건물 연식·층·구조)로 대신 판단하지 않는다 (R-503).
- 데이터로 확인할 수 없는 것(큰 도로 소음, 치안, 오르막 등)은 unsupported 에 짧게 적고 조건을 만들지 않는다 (R-504).
- 예산·보증금·면적·통학 시간으로 표현되는 말은 제외 조건을 만들지 않는다 (R-310, 조건 변경).
- 같은 대상을 다시 말하면 새 기준으로 조건 하나만 만든다 (R-510). 지금 조건은 context.exclusions.

{rules.rules_block(5)}
"""

EXPLAIN_SYSTEM = """너는 대학생 자취방 추천 결과를 설명하는 도우미다. 아래 '사실' JSON 만 근거로 한국어로 짧게 설명한다.

규칙
- 사실에 없는 매물, 가격, 도보시간, 점수, 순위, 건물을 만들지 않는다. 숫자는 사실에 있는 값을 그대로 쓴다 (반올림·계산·추정 금지).
- 점수와 순위는 서비스의 계산 결과다. 네가 다시 매기거나 바꾸지 않는다.
- policy 가 있으면 정책 안내다. 각 정책의 이름·내용·기관·신청 기간·링크를 사실 그대로 전한다.
  자격을 판정하지 않는다: "대상입니다/받을 수 있습니다/해당됩니다" 같은 말을 쓰지 않고, 연령 등 조건은 '공고 기준'으로만 전한다.
  지원 금액을 유지비·추천 순위와 합쳐 계산하지 않는다. 마지막에 disclaimer 와 공식 사이트를 안내한다.
  policy.available 이 false 면 정책 내용을 만들지 말고 reason 과 공식 사이트만 안내한다.
  policy.notes 는 빠뜨리지 않는다. kind=compare 면 어느 쪽이 유리한지 말하지 않고 항목별로 나란히 전한다.
  kind=apply 면 apply_method·documents·screening 을 표기 그대로 전하고, 없으면 공식 페이지 확인을 안내한다.
  kind=contract_safety 면 checklist 의 단계·항목·링크만 전한다 (항목을 더하지 않는다). 안전을 보장한다고 말하지 않는다.
- analysis.kind=excluded 면 단계별 빠진 수·사유 예시와 not_recommended 의 message 를 그대로 전한다 (사유를 새로 만들지 않는다).
- monthly_cost_manwon 은 '유지비(월세+관리비)'다. '월세'라고 부르지 않는다. travel_min_one_way 는 '통학 편도(도보) 분'이다.
- 광고 매물이므로 입주 가능하다고 단정하지 않는다. 안전하다고 단정하지 않는다.
- notes·limitations 에 있는 계산 불가 항목과 한계를 빠뜨리지 않는다.
- how_it_works 는 추천 방식 명세다. '왜 1위인지', '어떻게 추천하는지'는 이것과 결과 값으로 설명한다 (예: 통학 시간이 허용 시간 안이라 점수가 높다).
- 구성: (1) 반영한 조건 한 줄 (2) TOP 매물 각각 주소·유지비·보증금·통학(편도 분)·면적 (3) 비교가 있으면 바뀐 점 (4) 한계·다음에 할 수 있는 질문.
- analysis 가 있으면 그 분석 결과를 중심으로 설명한다. 분석은 참고용이며 추천 순위를 바꾸지 않는다고 밝힌다.
  실거래(market)는 과거 계약 기록이라 광고 매물의 현재 가격과 다를 수 있다. 편의시설(amenities) 거리는 직선거리다.
- 마크다운 목록은 써도 되지만 표는 쓰지 않는다. 8문장 안팎.
"""


TIMETABLE_SYSTEM = """너는 대학 시간표 캡처(에브리타임 등)를 읽는 도우미다. 보이는 수업 칸만 구조화한다.

규칙
- 칸마다 요일(월~일), 시작·종료 시각(HH:MM, 24시간), 건물, 과목을 적는다. 같은 과목이 여러 요일이면 요일마다 따로 적는다.
- 시각은 칸의 위치와 시간 눈금으로 읽는다. 1교시·2교시처럼 교시만 있으면 notes 에 남기고 추측으로 시각을 만들지 않는다.
- 건물은 강의실 표기에서 건물 부분만 (예: 'ECC B146' → 'ECC', '포관 B152' → '포관', '공학관 301' → '공학관'). 표기가 없으면 빈 문자열.
- 보이지 않거나 흐린 칸, 애매한 내용은 지어내지 말고 notes 에 짧게 적는다.
- 참고용 건물 목록이 있으면 표기를 그대로 두되, 같은 건물이면 그 이름을 써도 된다.
"""


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 부분 목록 (텍스트 조각 + 기타)
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


# ---------------------------------------------------------------- Gemini

def _default_chat_factory(api_key: str) -> Callable[[str], Any]:
    def make(model: str) -> Any:
        from langchain_google_genai import ChatGoogleGenerativeAI

        # 재시도는 아래 _call 이 한다 (종류별로 다르게, 전체 시간 상한 안에서)
        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, temperature=0,
                                      timeout=TIMEOUT_S, max_retries=0)
    return make


class _FallbackLLM:
    """모델 목록(주 모델 + 대체 모델)을 순서대로 쓴다. 공급자(Gemini, EXAONE)와 상관없는 재시도 규칙.

    - 혼잡·시간 초과(busy): 같은 모델로 RETRIES 번까지 간격을 늘려 다시 하고, 안 되면 다음 모델로.
    - 모델 없음(not_found)·사용 한도(quota, 429): 바로 다음 모델로 (한도는 모델별).
    - 인증·응답 형식 등: 다시 해도 같으므로 바로 실패.
    - 전체 시간이 TOTAL_BUDGET_S 를 넘으면 멈춘다 (사용자가 끝없이 기다리지 않게).
    """

    provider = "LLM"
    retries = RETRIES      # 혼잡일 때 같은 모델에서 다시 해 보는 횟수
    backoff_s = BACKOFF_S  # 재시도 간격 시작값 (두 배씩)
    total_budget_s: float = TOTAL_BUDGET_S  # 한 번의 extract/explain 이 쓰는 전체 시간 상한

    def __init__(self, models: list[str] | str, chat_factory: Callable[[str], Any], *,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.models = [models] if isinstance(models, str) else list(models)
        if not self.models:
            raise LLMUnavailable(f"{self.provider} 모델이 비어 있습니다.")
        self._factory = chat_factory
        self._chats: dict[str, Any] = {}
        self._sleep, self._clock = sleep, clock
        self.last_model: str | None = None  # 마지막으로 성공한 모델 (로그·확인용)

    @property
    def model_name(self) -> str:
        return self.models[0]

    def _chat(self, model: str) -> Any:
        if model not in self._chats:
            self._chats[model] = self._factory(model)
        return self._chats[model]

    def _remaining_s(self) -> float:
        """이번 extract/explain 의 남은 시간 (호출 한 번의 제한 시간으로 쓴다, 최소 5초)."""
        return max(5.0, getattr(self, "_deadline", float("inf")) - self._clock())

    def _call(self, what: str, fn: Callable[[Any], Any]) -> Any:
        start = self._clock()
        self._deadline = start + self.total_budget_s
        last: ErrorKind = "other"
        for model in self.models:
            for attempt in range(self.retries + 1):
                if self._clock() - start > self.total_budget_s:
                    raise LLMError(f"{self.provider} {what} 실패: 시간 초과", "busy")
                try:
                    out = fn(self._chat(model))
                    self.last_model = model
                    return out
                except LLMError:
                    raise
                except Exception as e:  # noqa: BLE001 — 외부 호출 실패. 키가 들어갈 수 있는 원문 대신 종류만 남긴다
                    last = classify_error(e)
                    if last == "busy" and attempt < self.retries:
                        self._sleep(self.backoff_s * 2 ** attempt)
                        continue
                    if last in ("busy", "not_found", "quota", "too_long"):
                        break  # 다음 모델 (한도는 모델마다 따로라 다른 모델은 될 수 있다)
                    raise LLMError(f"{self.provider} {what} 실패 ({last})", last) from e
        raise LLMError(f"{self.provider} {what} 실패 ({last}, 모델 {len(self.models)}개 모두)", last)


class GeminiLLM(_FallbackLLM):
    provider = "Gemini"

    def __init__(self, api_key: str, models: list[str] | str, *,
                 chat_factory: Callable[[str], Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        super().__init__(models, chat_factory or _default_chat_factory(api_key), sleep=sleep, clock=clock)

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        prompt = [
            ("system", extract_system()),
            ("human", "지금 조건:\n" + json.dumps(context, ensure_ascii=False) + "\n\n이번 메시지:\n" + message),
        ]

        def run(chat: Any) -> TurnExtraction:
            out = chat.with_structured_output(TurnExtraction).invoke(prompt)
            if isinstance(out, TurnExtraction):
                return out
            try:
                return TurnExtraction.model_validate(out)
            except Exception as e:  # noqa: BLE001
                raise LLMError("Gemini 응답 형식이 올바르지 않습니다", "bad_response") from e

        return self._call("조건 추출", run)

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        prompt = [
            ("system", plan_system()),
            ("human", "맥락:\n" + json.dumps(context, ensure_ascii=False) + "\n\n사용자 기호:\n" + request),
        ]

        def run(chat: Any) -> PlanDraft:
            out = chat.with_structured_output(PlanDraft).invoke(prompt)
            if isinstance(out, PlanDraft):
                return out
            try:
                return PlanDraft.model_validate(out)
            except Exception as e:  # noqa: BLE001
                raise LLMError("Gemini 응답 형식이 올바르지 않습니다", "bad_response") from e

        return self._call("제외 계획", run)

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        prompt = [
            ("system", EXPLAIN_SYSTEM),
            ("human", "사용자 메시지:\n" + user_message + "\n\n사실:\n" + json.dumps(facts, ensure_ascii=False)),
        ]
        return self._call("설명 생성", lambda chat: _content_text(chat.invoke(prompt).content).strip())

    def read_timetable(self, image_b64: str, mime_type: str, buildings: list[str]) -> TimetableRead:
        """시간표 이미지 → 수업 칸 (Gemini 이미지 입력 + 구조화 출력). 건물 표준화·검증은 서버가 한다."""
        from langchain_core.messages import HumanMessage, SystemMessage

        msgs = [
            SystemMessage(content=TIMETABLE_SYSTEM),
            HumanMessage(content=[
                {"type": "text", "text": "참고용 건물 목록: " + (", ".join(buildings) if buildings else "없음")},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
            ]),
        ]

        def run(chat: Any) -> TimetableRead:
            out = chat.with_structured_output(TimetableRead).invoke(msgs)
            if isinstance(out, TimetableRead):
                return out
            try:
                return TimetableRead.model_validate(out)
            except Exception as e:  # noqa: BLE001
                raise LLMError("Gemini 시간표 응답 형식이 올바르지 않습니다", "bad_response") from e

        return self._call("시간표 읽기", run)


def gemini_from_env() -> GeminiLLM:
    """GEMINI_MODEL (주 모델) + GEMINI_FALLBACK_MODELS (쉼표, 선택: 혼잡할 때 차례로 쓸 모델)."""
    key, model = _read("GEMINI_API_KEY"), (_read("GEMINI_MODEL") or "").strip()
    if not key or not model:
        raise LLMUnavailable("GEMINI_API_KEY 와 GEMINI_MODEL 을 BE/.env.local 에 설정해야 합니다.")
    fallbacks = [m.strip() for m in (_read("GEMINI_FALLBACK_MODELS") or "").split(",") if m.strip()]
    return GeminiLLM(key, list(dict.fromkeys([model, *fallbacks])))


# ---------------------------------------------------------------- EXAONE (OpenAI 호환)

JsonMode = Literal["prompt", "json_schema"]

_THOUGHT_RE = re.compile(r"<(thought|think|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


def _json_instruction(schema: dict[str, Any]) -> str:
    return ("\n\n출력 형식\n- 아래 JSON 스키마에 맞는 JSON 객체 하나만 출력한다. 설명 문장·코드 블록(```)·주석을 붙이지 않는다.\n"
            "- 값이 없으면 null 또는 빈 목록을 쓴다.\n스키마:\n" + json.dumps(schema, ensure_ascii=False))


def parse_json_object(text: str) -> Any:
    """모델 출력에서 JSON 객체 하나를 꺼낸다. 추론 태그(<thought> 등)·코드 블록을 걷어 낸다. 없으면 ValueError."""
    s = _THOUGHT_RE.sub("", text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.IGNORECASE)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON 객체 없음")
    return json.loads(s[start: end + 1])


def _default_openai_factory(api_key: str, base_url: str, timeout_s: float = TIMEOUT_S) -> Callable[[str], Any]:
    from openai import OpenAI

    # 재시도는 _call 이 한다 (SDK 자체 재시도 끔)
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=0)

    def make(model: str) -> Any:
        return (client, model)
    return make


class ExaoneLLM(_FallbackLLM):
    """LG EXAONE (OpenAI 호환 chat completions). LG EXAONE API 플랫폼, FriendliAI 등 base_url 만 바꿔 쓴다.

    - 구조화 출력: json_mode="json_schema" 면 response_format 으로 스키마를 넘기고, "prompt"(기본) 면 프롬프트에
      스키마를 넣어 JSON 만 받는다. 어느 쪽이든 서버가 pydantic 으로 다시 검증하고, 틀리면 bad_response.
    - 이미지 입력은 쓰지 않는다 (read_timetable → LLMUnavailable, 시간표 사진은 OCR 경로).
    """

    provider = "EXAONE"
    # 2026-11-15: 서버 전체 동시 요청 한도(429 "too many concurrent requests")가 간헐적으로 걸린다.
    # 실패 요청은 하루 한도(100회)에 세지 않으므로, 전체 시간 상한(TOTAL_BUDGET_S) 안에서 더 길게 기다려 다시 한다.
    retries = 4
    backoff_s = 2.0
    # 2026-11-15 실측: 추론 모델이라 조건 추출 10~25초, 설명은 60초 넘음('none' 은 400) → 설명은 AGENT_LLM_EXPLAIN=gemini.
    # 대체 공급자(Gemini)가 있으므로 혼잡할 때 오래 붙잡지 않는다: 호출 한 번 40초, 전체 50초 뒤 다음 공급자로.
    timeout_s = 40.0
    total_budget_s = 50.0

    def __init__(self, api_key: str, base_url: str, models: list[str] | str, *,
                 chat_factory: Callable[[str], Any] | None = None, json_mode: JsonMode = "prompt",
                 reasoning_effort: str | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        super().__init__(models, chat_factory or _default_openai_factory(api_key, base_url, self.timeout_s),
                         sleep=sleep, clock=clock)
        self.json_mode: JsonMode = json_mode
        self.reasoning_effort = reasoning_effort

    def _complete(self, chat: Any, messages: list[dict[str, str]], schema: tuple[str, dict[str, Any]] | None = None) -> str:
        client, model = chat
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
        if self.reasoning_effort:
            kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}
        if schema and self.json_mode == "json_schema":
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {"name": schema[0], "schema": schema[1]}}
        kwargs["timeout"] = min(self.timeout_s, self._remaining_s())
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    def _structured(self, what: str, system: str, user: str, model_cls: type) -> Any:
        schema = model_cls.model_json_schema()
        sys_text = system + (_json_instruction(schema) if self.json_mode == "prompt" else "")

        def run(chat: Any) -> Any:
            text = self._complete(chat, [{"role": "system", "content": sys_text}, {"role": "user", "content": user}],
                                  (model_cls.__name__, schema))
            try:
                return model_cls.model_validate(parse_json_object(text))
            except Exception as e:  # noqa: BLE001 — JSON 이 아니거나 스키마와 다름
                raise LLMError(f"EXAONE {what} 응답 형식이 올바르지 않습니다", "bad_response") from e

        return self._call(what, run)

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        user = "지금 조건:\n" + json.dumps(context, ensure_ascii=False) + "\n\n이번 메시지:\n" + message
        return self._structured("조건 추출", extract_system(), user, TurnExtraction)

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        user = "맥락:\n" + json.dumps(context, ensure_ascii=False) + "\n\n사용자 기호:\n" + request
        return self._structured("제외 계획", plan_system(), user, PlanDraft)

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        user = "사용자 메시지:\n" + user_message + "\n\n사실:\n" + json.dumps(facts, ensure_ascii=False)

        def run(chat: Any) -> str:
            text = self._complete(chat, [{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": user}])
            return _THOUGHT_RE.sub("", text).strip()

        return self._call("설명 생성", run)

    def read_timetable(self, image_b64: str, mime_type: str, buildings: list[str]) -> TimetableRead:
        raise LLMUnavailable("EXAONE 은 이미지 입력을 쓰지 않습니다. 시간표 사진은 OCR 로 읽습니다.")


def exaone_from_env() -> ExaoneLLM:
    """EXAONE_API_KEY, EXAONE_BASE_URL, EXAONE_MODEL (필수) + EXAONE_FALLBACK_MODELS, EXAONE_REASONING_EFFORT,
    EXAONE_JSON_MODE (prompt | json_schema). 주소·모델 이름은 추측해 넣지 않고 제공처 문서에서 확인한 값을 쓴다."""
    key, base, model = _read("EXAONE_API_KEY"), (_read("EXAONE_BASE_URL") or "").strip(), (_read("EXAONE_MODEL") or "").strip()
    if not key or not base or not model:
        raise LLMUnavailable("EXAONE_API_KEY, EXAONE_BASE_URL, EXAONE_MODEL 을 BE/.env.local 에 설정해야 합니다.")
    fallbacks = [m.strip() for m in (_read("EXAONE_FALLBACK_MODELS") or "").split(",") if m.strip()]
    mode = (_read("EXAONE_JSON_MODE") or "prompt").strip()
    return ExaoneLLM(key, base, list(dict.fromkeys([model, *fallbacks])),
                     json_mode="json_schema" if mode == "json_schema" else "prompt",
                     reasoning_effort=(_read("EXAONE_REASONING_EFFORT") or "").strip() or None)


_FACTORIES: dict[str, Callable[[], Any]] = {}  # 아래에서 채운다 (이름 → *_from_env)

# 공급자를 바꿔 다시 할 오류. 응답 형식 오류(bad_response)·기타는 바꾸지 않는다 (다른 모델도 같을 가능성, 호출만 늘어남).
SWITCH_KINDS = {"quota", "busy", "too_long", "not_found", "auth"}
# 혼잡(busy)으로 실패한 공급자는 이 시간 동안 건너뛴다 (매 메시지마다 50초씩 기다리지 않게). LLM_BUSY_COOLDOWN_S 로 바꾼다
BUSY_COOLDOWN_S = 300.0


def _next_midnight(now: float) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 0, 0, 0, 0, -1))


class ProviderChain:
    """주 공급자 → 대체 공급자 순서로 부른다 (LLM_PROVIDER + LLM_FALLBACK_PROVIDERS, 2026-11-16 기준 gemini → exaone).

    - 하루 한도(quota): 그 공급자를 오늘 자정(서버 현지 시각)까지 건너뛴다.
    - 혼잡(busy): 다음 공급자로 넘기고 그 공급자는 busy_cooldown_s(기본 5분) 동안 뒤로 미룬다
      (매 메시지마다 혼잡한 공급자를 50초씩 기다리지 않게). 다른 공급자가 모두 안 되면 미뤄 둔 공급자도 시도한다.
    - 토큰·크레딧 소진(402·token limit 등)도 하루 한도와 같이 처리한다.
    - 입력 토큰 초과(too_long)·모델 없음·인증 오류: 이번 호출만 다음 공급자로.
    - 응답 형식 오류 등: 바꾸지 않고 그대로 실패.
    - 시간표 사진(read_timetable)은 주 공급자에만 묻는다 → EXAONE 이면 LLMUnavailable → OCR (이미지를 외부로 보내지 않음).
    - last_model 은 '공급자:모델' (턴 기록용).
    """

    def __init__(self, providers: list[tuple[str, Any]], clock: Callable[[], float] = time.time,
                 busy_cooldown_s: float = BUSY_COOLDOWN_S, chain_budget_s: float = CHAIN_BUDGET_S,
                 budget_clock: Callable[[], float] = time.monotonic):
        if not providers:
            raise LLMUnavailable("쓸 수 있는 LLM 공급자가 없습니다 (키·모델 설정 확인).")
        self.providers = providers
        self.clock = clock
        self.busy_cooldown_s = busy_cooldown_s
        self.chain_budget_s = chain_budget_s
        self.budget_clock = budget_clock
        self._own_budget: dict[str, float | None] = {}
        self.exhausted_until: dict[str, float] = {}   # 하루 한도 → 자정까지
        self.busy_until: dict[str, float] = {}        # 혼잡 → 잠시 뒤로
        self.last_model: str | None = None
        self.last_provider: str | None = None

    def _order(self, now: float) -> list[tuple[str, Any]]:
        usable = [p for p in self.providers if self.exhausted_until.get(p[0], 0) <= now]
        fresh = [p for p in usable if self.busy_until.get(p[0], 0) <= now]
        return fresh + [p for p in usable if p not in fresh]  # 혼잡으로 미룬 공급자는 맨 뒤

    def _call(self, method: str, *args: Any) -> Any:
        now = self.clock()
        order = self._order(now)
        if not order:
            raise LLMError("모든 LLM 공급자가 오늘 사용 한도를 넘었습니다.", "quota")
        last: LLMError | None = None
        deadline = self.budget_clock() + self.chain_budget_s
        for name, llm in order:
            remaining = deadline - self.budget_clock()
            if last is not None and remaining < MIN_PROVIDER_BUDGET_S:
                break  # 앞 공급자가 시간을 다 써서 다음 공급자를 시도할 시간이 없음
            # 공급자를 바꿔도 사용자가 기다리는 전체 시간은 chain_budget_s 하나로 묶는다
            own = self._own_budget.setdefault(name, getattr(llm, "total_budget_s", None))  # 공급자 원래 상한
            if own is not None:
                llm.total_budget_s = min(own, remaining)
            try:
                out = getattr(llm, method)(*args)
            except LLMError as e:
                if e.kind not in SWITCH_KINDS:
                    raise
                if e.kind == "quota":
                    self.exhausted_until[name] = _next_midnight(now)
                elif e.kind == "busy":
                    self.busy_until[name] = now + self.busy_cooldown_s
                last = e
                continue
            self.busy_until.pop(name, None)
            self.last_provider = name
            self.last_model = f"{name}:{getattr(llm, 'last_model', None)}"
            return out
        assert last is not None
        raise LLMError(f"모든 LLM 공급자 실패 ({', '.join(n for n, _ in order)})", last.kind)

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        return self._call("extract", message, context)

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        return self._call("explain", user_message, facts)

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        return self._call("plan", request, context)

    def read_timetable(self, image_b64: str, mime_type: str, buildings: list[str]) -> TimetableRead:
        return self.providers[0][1].read_timetable(image_b64, mime_type, buildings)


def llm_from_env() -> AgentLLM:
    """LLM_PROVIDER (gemini 기본 | exaone) + LLM_FALLBACK_PROVIDERS (쉼표, 비우면 exaone) → 공급자 순서대로 쓰는 LLM.

    2026-11-16 사용자 결정: Gemini 먼저, 한도·혼잡·토큰 문제면 EXAONE.
    대체 공급자의 키·모델이 없으면 그 공급자는 빼고 쓴다. 주 공급자 설정이 없어도 대체 공급자가 있으면 그것만 쓴다.
    """
    primary = (_read("LLM_PROVIDER") or "gemini").strip().lower()
    fallback = _read("LLM_FALLBACK_PROVIDERS")
    fallback = "exaone" if fallback is None else fallback
    names = [primary] + [x.strip().lower() for x in fallback.split(",") if x.strip()]
    names = list(dict.fromkeys(names))
    unknown = [n for n in names if n not in _FACTORIES]
    if unknown:
        raise LLMUnavailable(f"알 수 없는 LLM 공급자: {', '.join(unknown)} (exaone 또는 gemini)")
    providers: list[tuple[str, Any]] = []
    first_error: LLMUnavailable | None = None
    for n in names:
        try:
            providers.append((n, _FACTORIES[n]()))
        except LLMUnavailable as e:
            first_error = first_error or e
    if not providers:
        raise first_error or LLMUnavailable("LLM 설정이 없습니다.")
    cooldown = float((_read("LLM_BUSY_COOLDOWN_S") or BUSY_COOLDOWN_S))
    return providers[0][1] if len(names) == 1 else ProviderChain(providers, busy_cooldown_s=cooldown)


_FACTORIES.update({"exaone": exaone_from_env, "gemini": gemini_from_env})


# ---------------------------------------------------------------- 테스트용

class ScriptedLLM:
    """테스트용: 미리 정한 추출 결과를 순서대로 돌려준다. explain 은 정한 문장(없으면 실패)을 돌려준다."""

    def __init__(self, extractions: list[TurnExtraction | Exception], explanations: list[str | Exception] | None = None,
                 plans: list[PlanDraft | Exception] | None = None):
        self._ex = deque(extractions)
        self._exp = deque(explanations or [])
        self._plans = deque(plans or [])
        self.plan_contexts: list[dict[str, Any]] = []
        self.contexts: list[dict[str, Any]] = []
        self.facts: list[dict[str, Any]] = []

    def extract(self, message: str, context: dict[str, Any]) -> TurnExtraction:
        self.contexts.append(context)
        x = self._ex.popleft()
        if isinstance(x, Exception):
            raise x
        return x

    def read_timetable(self, image_b64: str, mime_type: str, buildings: list[str]) -> TimetableRead:
        self.timetable_calls = getattr(self, "timetable_calls", 0) + 1
        x = self._ex.popleft()
        if isinstance(x, Exception):
            raise x
        return x  # type: ignore[return-value]

    def plan(self, request: str, context: dict[str, Any]) -> PlanDraft:
        self.plan_contexts.append(context)
        if not self._plans:
            raise LLMError("계획 없음 (테스트)")
        x = self._plans.popleft()
        if isinstance(x, Exception):
            raise x
        return x

    def explain(self, user_message: str, facts: dict[str, Any]) -> str:
        self.facts.append(facts)
        if not self._exp:
            raise LLMError("설명 없음 (테스트)")
        x = self._exp.popleft()
        if isinstance(x, Exception):
            raise x
        return x


def profile_context(current: AgentProfile, previous: AgentProfile | None, last_top: list[dict[str, Any]] | None,
                    history: list[dict[str, str]]) -> dict[str, Any]:
    """extract 에 주는 맥락. 큰 결과는 넣지 않는다."""
    return {
        "current_profile": current.model_dump(mode="json", exclude_defaults=True),
        "previous_profile": previous.model_dump(mode="json", exclude_defaults=True) if previous else None,
        "last_top": last_top,
        "recent_messages": history[-6:],
    }
