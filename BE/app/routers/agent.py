"""POST /agent/chat — LLM(Gemini → EXAONE, LLM_PROVIDER) + LangGraph 단일 에이전트.

- 기존 추천 API(/recommend 등)는 그대로다. 에이전트는 같은 통합안 엔진 함수를 직접 부른다.
- LLM 키·모델이 없거나 호출이 실패해도 500 을 내지 않고 status 로 알린다 (기존 추천은 영향 없음).
- 대화 상태는 서버 메모리에 session_id 별로 둔다 (개발용, 서버를 다시 켜면 사라짐).
"""

from __future__ import annotations

import base64
import binascii
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
from app.agent.schemas import (
    ChatRequest, ChatResponse, SlotRead, TimetableImageRequest, TimetableImageResponse, TimetableRead,
)
from app import timetable_ocr
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
    # LLM_PROVIDER (gemini 기본 | exaone) + LLM_FALLBACK_PROVIDERS. 설정을 바꾸면 서버를 다시 켠다 (캐시)
    return llm_from_env()


def _get_llm() -> AgentLLM:
    # 설정이 없으면 LLMUnavailable 을 던진다 (lru_cache 는 예외를 저장하지 않으므로 키를 넣으면 다음 요청부터 동작)
    return _provider_llm()


_graph = build_graph(_get_llm, trace_sink=write_trace)


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # 확인 질문(학교 변경·전체 되돌리기)을 기다리는 중이면 이번 메시지를 답으로 이어 간다
    state = run_turn(_graph, req.session_id, req.message,
                     req.profile.model_dump(mode="json") if req.profile else None, req.mode)
    return ChatResponse(session_id=req.session_id, **to_response_parts(state))


@router.post("/timetable-image", response_model=TimetableImageResponse)
def timetable_image(req: TimetableImageRequest) -> TimetableImageResponse:
    """시간표 캡처 → 수업 칸 미리보기 (저장하지 않음, FE 확인 화면에서 고친 뒤 시간표에 넣는다).

    2026-11-16 사용자 결정: 시간표 사진은 서버의 OpenCV·OCR 로 읽는다 (외부로 보내지 않음).
    같은 날 바뀐 결정: OCR 을 설치할 수 없는 서버(Render 무료, 메모리 512MB)는 TIMETABLE_LLM_FALLBACK=on 일 때만
    Gemini(주 공급자) 이미지 입력으로 읽는다. 사진은 저장하지 않고, 결과에 Gemini 로 읽었다고 밝힌다. 로컬 기본값은 off.
    """
    res = ocr_preview(req)
    if res.status != "ocr_unavailable" or (_read("TIMETABLE_LLM_FALLBACK") or "off").strip().lower() != "on":
        return res
    try:
        read = _get_llm().read_timetable(req.image_base64, req.mime_type, res.buildings)
    except LLMUnavailable:
        return res
    except LLMError as e:
        msg = "AI 서버가 혼잡해 사진을 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요." if e.kind == "busy" else \
            "사진을 읽지 못했습니다. 다른 캡처로 다시 시도하거나 직접 입력해 주세요."
        return TimetableImageResponse(status="llm_error", message=msg, buildings=res.buildings)
    # A안 (11-16): 칸의 요일·시각은 OpenCV 격자 위치로 다시 잰다 (AI 는 시각을 30분·1시간 단위로 어림한다).
    # 격자를 못 찾으면 AI 시각을 그대로 쓰고 안내한다
    grid_notes: list[str] = []
    try:
        data = base64.b64decode(req.image_base64, validate=True)
        aligned, grid_notes = timetable_ocr.align_with_grid(
            timetable_ocr.grid_blocks(data), [s.model_dump() for s in read.slots])
        read = TimetableRead(slots=[SlotRead.model_validate(s) for s in aligned], notes=read.notes)
    except (timetable_ocr.OcrError, timetable_ocr.OcrUnavailable, binascii.Error, ValueError):
        grid_notes = ["시간표 격자를 찾지 못해 AI 가 읽은 시각을 그대로 썼습니다. 시간을 확인해 주세요."]
    slots = tools.check_timetable_read(read, req.university)
    need = sum(1 for s in slots if s.issue)
    msg = (f"수업 {len(slots)}칸을 읽었어요." + (f" {need}칸은 확인이 필요해요." if need else "")
           + " 맞는지 확인한 뒤 시간표에 넣어 주세요.") if slots else "수업 칸을 찾지 못했습니다. 직접 입력해 주세요."
    notes = [*read.notes, *grid_notes,
             "이 서버에는 글자 인식(OCR)이 없어 과목·강의실 글자를 Google Gemini 로 읽었습니다. 사진은 저장하지 않습니다."]
    return TimetableImageResponse(status="ok", message=msg, slots=slots, notes=notes, buildings=res.buildings)
