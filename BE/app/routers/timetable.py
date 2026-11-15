"""시간표 캡처 → 수업 칸 미리보기 (OpenCV + EasyOCR, LLM·외부 API 없음).

POST /timetable/ocr : /agent/timetable-image(Gemini) 와 같은 요청·응답 형식. FE 확인 화면을 그대로 쓴다.
이미지는 서버 안에서만 읽고 저장하지 않는다. OCR 패키지가 없으면 status=ocr_unavailable.
"""

import base64
import binascii

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app import timetable_ocr
from app.agent import tools
from app.agent.schemas import SlotRead, TimetableImageRequest, TimetableImageResponse, TimetableRead

router = APIRouter(prefix="/timetable", tags=["timetable"])


@router.post("/ocr", response_model=TimetableImageResponse)
async def timetable_ocr_preview(req: TimetableImageRequest) -> TimetableImageResponse:
    # OCR 은 수 초 걸리는 CPU 작업이라 이벤트 루프를 막지 않게 스레드에서 돌린다
    return await run_in_threadpool(ocr_preview, req)


def ocr_preview(req: TimetableImageRequest) -> TimetableImageResponse:
    """OCR 미리보기 본체 (동기). /agent/timetable-image 가 이미지 미지원 LLM 일 때도 이 함수를 쓴다."""
    buildings = tools.usable_buildings(req.university) if tools.university_listing_count(req.university) else []
    try:
        data = base64.b64decode(req.image_base64, validate=True)
    except (binascii.Error, ValueError):
        return TimetableImageResponse(status="ocr_error", message="이미지 데이터를 읽을 수 없습니다.", buildings=buildings)
    try:
        result = timetable_ocr.read_timetable_image(data)
    except timetable_ocr.OcrUnavailable:
        return TimetableImageResponse(
            status="ocr_unavailable",
            message="이 서버에는 사진 인식 기능이 설치되어 있지 않습니다. 직접 입력해 주세요.",
            buildings=buildings,
        )
    except timetable_ocr.OcrError as e:
        return TimetableImageResponse(
            status="ocr_error",
            message=f"시간표 형식을 읽지 못했습니다 ({e}). 시간표 화면 전체를 캡처했는지 확인하거나 직접 입력해 주세요.",
            buildings=buildings,
        )

    read = TimetableRead(
        slots=[SlotRead(day=s.day, start=s.start, end=s.end, building_text=s.room_text, course=s.course)
               for s in result.slots],
        notes=result.notes,
    )
    # 시각 검증 + 강의실 표기('캠B134') → 표준 건물 이름. 모르는 건물·개인 일정은 issue 로 표시
    slots = tools.check_timetable_read(read, req.university)
    need = sum(1 for s in slots if s.issue)
    msg = (f"수업 {len(slots)}칸을 읽었어요." + (f" {need}칸은 확인이 필요해요." if need else "")
           + " 맞는지 확인한 뒤 시간표에 넣어 주세요.") if slots else "수업 칸을 찾지 못했습니다. 직접 입력해 주세요."
    return TimetableImageResponse(status="ok", message=msg, slots=slots, notes=read.notes, buildings=buildings)
