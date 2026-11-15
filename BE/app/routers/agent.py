"""POST /agent/chat — LLM(Gemini 주 모델 / EXAONE 대체) + LangGraph 챗봇. 통합안 추천을 직접 부른다.

- 추천 API(/recommend)는 그대로다. 챗봇은 같은 엔진 함수(app.scoring.recommend)를 부른다.
- LLM 키·모델이 없거나 호출이 실패해도 500 을 내지 않고 status 로 알린다 (추천 API 는 영향 없음).
- 대화 상태는 서버 메모리에 session_id 별로 둔다 (개발용, 서버를 다시 켜면 사라짐).
- 턴 기록: BE/logs/agent_trace.jsonl (AGENT_TRACE_PATH 로 바꾸고, off 면 끈다). 커밋하지 않는다.
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app.agent.graph import build_graph, run_turn, to_response_parts
from app.agent.llm import AgentLLM, llm_from_env
from app.agent.schemas import AgentProfile, ChatRequest, ChatResponse
from app.config import BE_DIR, _read, get_settings
from app.data import load_properties, load_travel_times
from app.exclusions import KakaoPlaceSearcher

DEFAULT_TRACE_PATH = BE_DIR / "logs" / "agent_trace.jsonl"
_trace_lock = threading.Lock()


def _trace_path() -> Path | None:
    value = os.environ["AGENT_TRACE_PATH"] if "AGENT_TRACE_PATH" in os.environ else _read("AGENT_TRACE_PATH")
    if value is None:
        return DEFAULT_TRACE_PATH
    if not value.strip() or value.strip().lower() == "off":
        return None
    p = Path(value.strip())
    return p if p.is_absolute() else BE_DIR / p


def write_trace(trace: dict[str, Any]) -> None:
    path = _trace_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _trace_lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")


@lru_cache(maxsize=1)
def _llm() -> AgentLLM:
    return llm_from_env()


def _get_llm() -> AgentLLM:
    # 설정이 없으면 LLMUnavailable (lru_cache 는 예외를 저장하지 않으므로 키를 넣으면 다음 요청부터 동작)
    return _llm()


def _get_data():
    props = load_properties()  # lru_cache
    return props, _travel(props)


_travel_cache: dict[int, dict[str, dict[str, float]]] = {}


def _travel(props) -> dict[str, dict[str, float]]:
    key = id(props)
    if key not in _travel_cache:
        _travel_cache.clear()
        _travel_cache[key] = load_travel_times(props)
    return _travel_cache[key]


_kakao: dict[str, Any] = {}


def _get_searcher(p: AgentProfile) -> Any:
    """카카오 장소 조건이 있을 때만 장소 검색을 연결한다 (키가 없으면 None → 확인하지 않고 안내)."""
    if not any(c.uses_places for plan in p.exclusions for c in plan.conditions):
        return None
    key = get_settings().kakao_rest_api_key
    if not key:
        return None
    if "client" not in _kakao:
        from app.tools.kakao import KakaoClient

        _kakao["client"] = KakaoClient(key)
    return KakaoPlaceSearcher(_kakao["client"])


router = APIRouter(prefix="/agent", tags=["agent"])
_graph = build_graph(_get_llm, _get_data, _get_searcher, trace_sink=write_trace)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    state = run_turn(_graph, req.session_id, req.message, req.profile.model_dump(mode="json") if req.profile else None)
    return ChatResponse(session_id=req.session_id, **to_response_parts(state))
