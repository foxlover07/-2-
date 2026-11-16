"""LLM 호출 실험 스크립트.

1) 일반 호출: 시스템 프롬프트 + 질문 → 답변과 사용 토큰 출력
2) JSON 호출: 자연어 선호를 구조화된 JSON 으로 추출 (실패 시 기본값)

실행 (BE 폴더에서):
    uv run python scripts/ask.py                 # 1, 2 모두
    uv run python scripts/ask.py chat "질문"      # 일반 호출만
    uv run python scripts/ask.py extract "문장"   # JSON 호출만
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

# BE/ 를 import 경로에 넣어 app.data 를 재사용
BE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_DIR))

from app.data import load_properties  # noqa: E402

load_dotenv(BE_DIR / ".env")

SYSTEM_PROMPT = "너는 대학생 자취방 추천 도우미야. 모르면 모른다고 답해."
DEFAULT_QUESTION = "이대 근처 원룸 구할 때 보증금이랑 월세 중에 뭘 더 따져봐야 해?"
DEFAULT_PREFERENCE = (
    "공대 수업이 주 4번이고 ECC는 주 2번 가. "
    "월 80만원 이하였으면 좋겠고 통학이 더 중요해."
)

DEFAULT_PREFS: dict[str, Any] = {
    "visits": {},
    "budget_manwon": None,
    "cost_importance": 3,
    "walk_importance": 3,
}


# ---------------------------------------------------------------- 공통

def make_client() -> tuple[OpenAI, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("LLM_MODEL")
    missing = [k for k, v in (("OPENAI_API_KEY", api_key), ("LLM_MODEL", model)) if not v]
    if missing:
        sys.exit(f"[오류] BE/.env 에 {', '.join(missing)} 값을 넣어주세요. (.env.example 참고)")
    base_url = os.getenv("OPENAI_BASE_URL") or None  # 비우면 OpenAI 기본 엔드포인트
    # 무료 모델은 429(사용량 제한)가 잦아서 SDK 기본(2회)보다 재시도를 늘림.
    # SDK 가 지수 백오프 + Retry-After 헤더를 알아서 처리한다.
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=5), model


def print_usage(usage: Any) -> None:
    if usage is None:
        print("[토큰] 제공자가 사용량을 반환하지 않음")
        return
    print(
        f"[토큰] 입력 {usage.prompt_tokens} / 출력 {usage.completion_tokens} "
        f"/ 합계 {usage.total_tokens}"
    )


# ---------------------------------------------------------------- 1) 일반 호출

def chat(question: str) -> str:
    client, model = make_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )
    answer = resp.choices[0].message.content or ""
    print(f"[질문] {question}")
    print(f"[답변] {answer}")
    print_usage(resp.usage)
    return answer


# ---------------------------------------------------------------- 2) JSON 호출

def spot_names() -> list[str]:
    """CSV 의 spot1~4_name 에서 강의동 이름 목록 (등장 순서 유지, 중복 제거)."""
    names: dict[str, None] = {}
    for p in load_properties():
        for i in range(1, 5):
            name = getattr(p, f"spot{i}_name")
            if name:
                names[name] = None
    return list(names)


def build_extract_prompt(spots: list[str]) -> str:
    return (
        "사용자의 자취방 선호를 아래 JSON 형식으로만 출력해. 설명이나 코드블록 없이 JSON 하나만.\n"
        '{"visits": {강의동: 주당 방문 횟수(정수)}, "budget_manwon": 숫자 또는 null, '
        '"cost_importance": 1~5 정수, "walk_importance": 1~5 정수}\n'
        f"- visits 의 키는 반드시 다음 중에서만 골라: {json.dumps(spots, ensure_ascii=False)}\n"
        "  (예: '공대' → '공과대학'). 목록에 없는 곳은 넣지 마.\n"
        "- budget_manwon 은 월 주거비 상한(만원). 언급이 없으면 null.\n"
        "- 비용/통학 중요도는 언급이 없으면 3. 더 중요하다고 한 쪽을 높게.\n"
    )


def _extract_json_text(text: str) -> str:
    """```json ... ``` 코드블록이나 앞뒤 잡담이 섞여도 첫 {...} 를 꺼낸다."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def _clamp_importance(value: Any, default: int) -> int:
    try:
        return max(1, min(5, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def parse_preferences(text: str, spots: list[str]) -> tuple[dict[str, Any], bool]:
    """LLM 출력 → 정규화된 선호 dict. (결과, 파싱 성공 여부) 반환. 실패 시 기본값."""
    try:
        raw = json.loads(_extract_json_text(text))
        if not isinstance(raw, dict):
            raise ValueError("JSON 객체가 아님")
    except (json.JSONDecodeError, ValueError):
        return dict(DEFAULT_PREFS, visits={}), False

    allowed = set(spots)
    visits: dict[str, int] = {}
    raw_visits = raw.get("visits")
    if isinstance(raw_visits, dict):
        for name, count in raw_visits.items():
            if name not in allowed:
                continue  # CSV 에 없는 강의동은 버림
            try:
                n = int(count)
            except (TypeError, ValueError):
                continue
            if n > 0:
                visits[name] = n

    budget = raw.get("budget_manwon")
    try:
        budget = float(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None

    return {
        "visits": visits,
        "budget_manwon": budget,
        "cost_importance": _clamp_importance(raw.get("cost_importance"), 3),
        "walk_importance": _clamp_importance(raw.get("walk_importance"), 3),
    }, True


def extract(sentence: str) -> dict[str, Any]:
    client, model = make_client()
    spots = spot_names()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": build_extract_prompt(spots)},
            {"role": "user", "content": sentence},
        ],
        temperature=0,
    )
    text = resp.choices[0].message.content or ""
    prefs, ok = parse_preferences(text, spots)
    print(f"[문장] {sentence}")
    print(f"[원본 출력] {text}")
    if not ok:
        print("[경고] JSON 파싱 실패 → 기본값 사용")
    print(f"[추출 결과] {json.dumps(prefs, ensure_ascii=False)}")
    print_usage(resp.usage)
    return prefs


# ---------------------------------------------------------------- 진입점

def main(argv: list[str]) -> None:
    mode = argv[1] if len(argv) > 1 else "all"
    arg = " ".join(argv[2:]) or None
    if mode == "chat":
        chat(arg or DEFAULT_QUESTION)
    elif mode == "extract":
        extract(arg or DEFAULT_PREFERENCE)
    elif mode == "all":
        print("=== 1) 일반 호출 ===")
        chat(DEFAULT_QUESTION)
        print("\n=== 2) JSON 호출 ===")
        extract(DEFAULT_PREFERENCE)
    else:
        sys.exit(f"사용법: {argv[0]} [chat|extract|all] [텍스트]")


if __name__ == "__main__":
    # Windows 콘솔에서 한글 깨짐 방지
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main(sys.argv)
    except AuthenticationError as e:
        sys.exit(f"[오류] 인증 실패(401): BE/.env 의 OPENAI_API_KEY 를 확인하세요. ({e.message})")
    except RateLimitError as e:
        # OpenRouter 는 실제 원인을 body.metadata.raw 에 담아 준다
        meta = e.body.get("metadata", {}) if isinstance(e.body, dict) else {}
        detail = meta.get("raw") or e.message
        sys.exit(
            "[오류] 사용량 제한(429): 재시도했지만 계속 막혔습니다. 잠시 후 다시 실행하거나, "
            f"LLM_MODEL 을 다른 모델로 바꿔보세요.\n  원인: {detail}"
        )
    except APIConnectionError as e:
        sys.exit(f"[오류] 연결 실패: OPENAI_BASE_URL 과 네트워크를 확인하세요. ({e})")
    except APIStatusError as e:
        sys.exit(f"[오류] API 오류 {e.status_code}: {e.message}")
