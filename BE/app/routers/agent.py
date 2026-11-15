"""POST /agent/chat — LLM(EXAONE 기본, LLM_PROVIDER) + LangGraph 단일 에이전트.

- 기존 추천 API(/recommend 등)는 그대로다. 에이전트는 같은 통합안 엔진 함수를 직접 부른다.
- LLM 키·모델이 없거나 호출이 실패해도 500 을 내지 않고 status 로 알린다 (기존 추천은 영향 없음).
- 대화 상태는 서버 메모리에 session_id 별로 둔다 (개발용, 서버를 다시 켜면 사라짐).
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
from app.agent import tools
from app.agent.llm import AgentLLM, LLMError, LLMUnavailable, llm_from_env
from app.agent.schemas import ChatRequest, ChatResponse, TimetableImageRequest, TimetableImageResponse
from app.config import BE_DIR, _read
from app.routers.timetable import ocr_preview

# 턴 기록 파일 (JSONL, 한 줄 = 한 턴). AGENT_TRACE_PATH 로 바꾸고, 빈 값이면 끈다. BE/logs/ 는 커밋하지 않는다.
DEFAULT_TRACE_PATH = BE_DIR / "logs" / "agent_trace.jsonl"
_trace_lock = threading.Lock()


def _trace_path() -> Path | None:
    value = os.environ["AGENT_TRACE_PATH"] if "AGENT_TRACE_PATH" in os.environ else _read("AGENT_TRACE_PATH")
    if value is None:
        return DEFAULT_TRACE_PATH
    if not value.strip():
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

router = APIRouter(prefix="/agent", tags=["agent"])


@lru_cache(maxsize=1)
def _provider_llm() -> AgentLLM:
    # LLM_PROVIDER (exaone 기본 | gemini). 설정을 바꾸면 서버를 다시 켠다 (캐시)
    return llm_from_env()


def _get_llm() -> AgentLLM:
    # 설정이 없으면 LLMUnavailable 을 던진다 (lru_cache 는 예외를 저장하지 않으므로 키를 넣으면 다음 요청부터 동작)
    return _provider_llm()


_graph = build_graph(_get_llm, trace_sink=write_trace)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # 확인 질문(학교 변경·전체 되돌리기)을 기다리는 중이면 이번 메시지를 답으로 이어 간다
    state = run_turn(_graph, req.session_id, req.message,
                     req.profile.model_dump(mode="json") if req.profile else None)
    return ChatResponse(session_id=req.session_id, **to_response_parts(state))


@router.post("/timetable-image", response_model=TimetableImageResponse)
def timetable_image(req: TimetableImageRequest) -> TimetableImageResponse:
    """시간표 캡처 → 수업 칸 미리보기 (저장하지 않음, FE 확인 화면에서 고친 뒤 시간표에 넣는다).

    이미지는 Gemini 로 보내 읽기만 하고 서버에 저장하지 않는다.
    """
    buildings = tools.usable_buildings(req.university) if tools.university_listing_count(req.university) else []
    try:
        read = _get_llm().read_timetable(req.image_base64, req.mime_type, buildings)
    except LLMUnavailable:
        # 이미지 입력이 없는 LLM(EXAONE)이거나 LLM 설정이 없으면 OpenCV·OCR 로 읽는다 (외부 전송 없음)
        return ocr_preview(req)
    except LLMError as e:
        msg = "AI 서버가 혼잡해 사진을 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요." if e.kind == "busy" else             "사진을 읽지 못했습니다. 다른 캡처로 다시 시도하거나 직접 입력해 주세요."
        return TimetableImageResponse(status="llm_error", message=msg, buildings=buildings)
    slots = tools.check_timetable_read(read, req.university)
    need = sum(1 for s in slots if s.issue)
    msg = (f"수업 {len(slots)}칸을 읽었어요." + (f" {need}칸은 확인이 필요해요." if need else "") +
           " 맞는지 확인한 뒤 시간표에 넣어 주세요.") if slots else "수업 칸을 찾지 못했습니다. 직접 입력해 주세요."
    return TimetableImageResponse(status="ok", message=msg, slots=slots, notes=read.notes, buildings=buildings)
