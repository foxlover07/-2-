"""시간표 캡처 → 수업 칸 (OpenCV + EasyOCR, LLM 없음).

에브리타임형 격자 시간표를 읽는다:
- 요일·시각은 **칸 위치**로 정한다 (OpenCV). 글자를 잘못 읽어도 시각은 틀리지 않는다.
  · 세로 격자선 → 요일 열 경계, 가로 격자선 → 1시간 간격, 왼쪽 시각 눈금(OCR 숫자) → 몇 시인지
  · 열마다 오른쪽 여백의 배경색을 세로로 훑어 색이 칠해진 구간 = 수업 칸
- 칸 안 글자만 OCR 로 읽는다: 과목명 · 교수명 · 강의실(마지막 줄, 예 '캠B134'). 강의실 줄이 없으면
  개인 일정(랩미팅 등)일 수 있으므로 건물 없음으로 둔다.

선택 설치: opencv-python-headless, easyocr (BE pyproject 의 `ocr` 그룹). 없으면 OcrUnavailable.
이미지는 저장하지 않는다. 결과는 확인 화면용 미리보기다 (app.agent.tools.check_timetable_read 로 검증).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

DAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

# 강의실 표기: 건물 줄임말(한글·영문) + 선택적 알파벳 + 숫자 (예: 캠B134, 학109, 포366, 경영313, ECC B142)
# 건물 부분은 한글만 또는 영문만 (캠B134 → '캠', ECC B142 → 'ECC')
_ROOM_RE = re.compile(r"^([가-힣]{1,6}|[A-Za-z]{2,8})\s*([A-Za-z]?\d{2,4}[\w-]*)\s*(호|호실)?$")
_SNAP_MIN = 5


class OcrUnavailable(RuntimeError):
    """OpenCV·EasyOCR 이 설치되지 않음."""


class OcrError(RuntimeError):
    """이미지는 받았지만 시간표 격자로 읽지 못함 (형식이 다르거나 흐림)."""


@dataclass
class OcrSlot:
    day: str
    start: str
    end: str
    building_text: str  # 강의실 줄에서 건물 부분 (예: '캠'). 없으면 ''
    room_text: str      # OCR 이 읽은 강의실 줄 그대로 (예: '캠B134')
    course: str | None


@dataclass
class OcrResult:
    slots: list[OcrSlot] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 의존성

def _import_cv():
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover - 설치 환경에 따라
        raise OcrUnavailable("OpenCV 가 설치되지 않았습니다 (opencv-python-headless).") from e
    return cv2, np


_reader_lock = threading.Lock()


@lru_cache(maxsize=1)
def _reader() -> Any:
    """EasyOCR Reader (한국어·영어). 처음 한 번 만들 때 수십 초 걸리고, 모델이 없으면 내려받는다."""
    try:
        import easyocr  # noqa: PLC0415
    except ImportError as e:  # pragma: no cover
        raise OcrUnavailable("EasyOCR 이 설치되지 않았습니다 (easyocr).") from e
    return easyocr.Reader(["ko", "en"], gpu=False, verbose=False)


def is_available() -> bool:
    try:
        _import_cv()
        import easyocr  # noqa: F401, PLC0415
    except (OcrUnavailable, ImportError):
        return False
    return True


# ---------------------------------------------------------------- 격자

def _group(positions: list[int], gap: int = 3) -> list[int]:
    """붙어 있는 좌표 묶음 → 가운데 값."""
    out: list[list[int]] = []
    for p in positions:
        if out and p - out[-1][-1] <= gap:
            out[-1].append(p)
        else:
            out.append([p])
    return [round(sum(g) / len(g)) for g in out]


def _line_mask(img, np):
    """격자선 후보: 밝은 회색(채도 없음)."""
    b, g, r = (img[:, :, i].astype(int) for i in range(3))
    mx = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    return (mx - mn <= 6) & (mn >= 185) & (mx <= 242)


def _find_columns(lines, np) -> list[tuple[int, int]]:
    h, _w = lines.shape
    xs = _group([int(x) for x in np.where(lines.sum(axis=0) > h * 0.3)[0]])
    if len(xs) < 3:
        raise OcrError("세로 격자선을 찾지 못했습니다.")
    spans = [(a, b) for a, b in zip(xs, xs[1:])]
    widest = max(b - a for a, b in spans)
    # 왼쪽 시각 눈금 열은 요일 열보다 좁다
    cols = [(a, b) for a, b in spans if b - a >= widest * 0.7]
    if not 1 <= len(cols) <= 7:
        raise OcrError(f"요일 열 수가 이상합니다 ({len(cols)}개).")
    return cols


def _find_hour_lines(lines, cols, np) -> list[int]:
    """요일 열 영역에서 가로 격자선. 칸에 일부 가려진 선도 잡도록 기준을 낮게 둔다."""
    x0, x1 = cols[0][0], cols[-1][1]
    sub = lines[:, x0:x1]
    ys = _group([int(y) for y in np.where(sub.sum(axis=1) > (x1 - x0) * 0.35)[0]])
    if len(ys) < 3:
        raise OcrError("가로 격자선을 찾지 못했습니다.")
    diffs = sorted(b - a for a, b in zip(ys, ys[1:]))
    pitch = diffs[len(diffs) // 2]
    # 가장 긴 '같은 간격' 연속 구간만 시간 격자로 본다 (아래쪽 구분선 등 제외)
    best: list[int] = []
    run = [ys[0]]
    for a, b in zip(ys, ys[1:]):
        if abs((b - a) - pitch) <= pitch * 0.12 or abs((b - a) - 2 * pitch) <= pitch * 0.12:
            run.append(b)
        else:
            best = max(best, run, key=len)
            run = [b]
    best = max(best, run, key=len)
    if len(best) < 3:
        raise OcrError("시간 격자 간격을 찾지 못했습니다.")
    # 칸에 완전히 가려진 선을 간격으로 채운다
    first, last = best[0], best[-1]
    n = round((last - first) / pitch)
    step = (last - first) / n
    return [round(first + i * step) for i in range(n + 1)]


def _hour_labels(img, cols, hour_lines, reader) -> int:
    """왼쪽 눈금 숫자를 읽어 첫 가로선이 몇 시인지 정한다 (12시 다음 1 → 13시)."""
    x1 = cols[0][0]
    top, bottom = hour_lines[0], hour_lines[-1]
    pitch = (bottom - top) / (len(hour_lines) - 1)
    strip = img[max(0, top - 5): bottom, 0:x1]
    found: list[tuple[int, int]] = []  # (선 번호, 숫자)
    for box, text, conf in reader.readtext(strip, allowlist="0123456789", detail=1):
        digits = re.sub(r"\D", "", text)
        if not digits or conf < 0.3:
            continue
        y_top = min(p[1] for p in box) + max(0, top - 5)
        idx = int((y_top - top) // pitch)  # 숫자는 자기 선 바로 아래에 있다
        if 0 <= idx < len(hour_lines) and 1 <= int(digits) <= 24:
            found.append((idx, int(digits)))
    if not found:
        raise OcrError("왼쪽 시각 눈금을 읽지 못했습니다.")
    found.sort()
    # 12시간 표기 → 24시간: 위에서 아래로 가며 숫자가 작아지면(12 → 1) 그다음부터 오후
    offset = 0
    prev: int | None = None
    starts: dict[int, int] = {}  # 첫 선의 시각 후보 → 표 수
    for idx, h in found:
        val = h + offset
        if prev is not None and val <= prev:
            offset += 12
            val = h + offset
        prev = val
        starts[val - idx] = starts.get(val - idx, 0) + 1
    # 숫자 하나를 잘못 읽어도 나머지가 맞으면 다수결로 정한다
    start = max(starts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
    if not 0 <= start <= 23:
        raise OcrError("시각 눈금이 이상합니다.")
    return start


def _days(img, cols, hour_lines, reader, notes: list[str]) -> list[str]:
    """격자 위쪽에 요일 줄이 보이면 읽고, 없으면 왼쪽부터 월요일로 가정한다."""
    top = hour_lines[0]
    region = img[max(0, top - 160): max(0, top - 2), cols[0][0]: cols[-1][1]]
    labels: dict[int, str] = {}
    if region.size:
        for box, text, conf in reader.readtext(region, detail=1):
            day = next((d for d in DAY_ORDER if d in text), None)
            if day is None or conf < 0.3:
                continue
            cx = sum(p[0] for p in box) / 4 + cols[0][0]
            for i, (a, b) in enumerate(cols):
                if a <= cx < b:
                    labels[i] = day
    if len(labels) == len(cols) and len(set(labels.values())) == len(cols):
        return [labels[i] for i in range(len(cols))]
    if labels:
        first_i, first_day = min(labels.items())
        start = DAY_ORDER.index(first_day) - first_i
        if 0 <= start and start + len(cols) <= len(DAY_ORDER):
            notes.append("요일 줄 일부만 읽혀 나머지 요일은 순서대로 채웠습니다. 요일을 확인해 주세요.")
            return DAY_ORDER[start: start + len(cols)]
    notes.append(f"요일 줄이 보이지 않아 왼쪽 열부터 {DAY_ORDER[0]}~{DAY_ORDER[len(cols) - 1]}요일로 가정했습니다. 요일을 확인해 주세요.")
    return DAY_ORDER[: len(cols)]


# ---------------------------------------------------------------- 칸

def _blocks_in_column(img, col, y_top, y_bottom, min_len, np) -> list[tuple[int, int]]:
    """열 오른쪽 여백(글자가 거의 없는 곳)의 배경색을 세로로 훑어 색이 칠해진 구간을 찾는다.
    색이 바뀌면 다른 칸으로 나눈다 (쉬는 시간 없이 붙은 수업)."""
    a, b = col
    strip = img[:, max(a, b - 14): b - 5].astype(int)
    med = np.median(strip, axis=1)
    lo, hi = med.min(axis=1), med.max(axis=1)
    blank = (lo >= 246) | ((hi - lo) < 4)  # 흰 배경 또는 회색 격자선
    runs: list[tuple[int, int]] = []
    start = None
    ref = None
    for y in range(y_top, y_bottom + 1):
        colored = not blank[y]
        if start is not None and (not colored or abs(med[y] - ref).max() > 10):
            if y - start >= min_len:
                runs.append((start, y))
            start, ref = None, None
        if colored and start is None:
            start, ref = y, med[y]
    if start is not None and y_bottom - start >= min_len:
        runs.append((start, y_bottom))
    return runs


def _to_hhmm(y: float, first_line: int, pitch: float, start_hour: int) -> str:
    minutes = round((start_hour + (y - first_line) / pitch) * 60 / _SNAP_MIN) * _SNAP_MIN
    minutes = max(0, min(minutes, 23 * 60 + 55))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_block_text(lines: list[str]) -> tuple[str | None, str, str]:
    """칸 글자 줄 → (과목명, 강의실 줄, 건물 표기). 강의실 줄은 마지막의 '줄임말+번호' 줄."""
    room_i = None
    for i in range(len(lines) - 1, -1, -1):
        if _ROOM_RE.match(lines[i].strip()):
            room_i = i
            break
    if room_i is None:
        course = "".join(lines).strip() or None
        return course, "", ""
    room = lines[room_i].strip()
    building = _ROOM_RE.match(room).group(1)  # type: ignore[union-attr]
    # 강의실 바로 앞 줄은 교수명 (한글 2~4자) 이면 과목명에서 뺀다
    head = lines[:room_i]
    if head and re.fullmatch(r"[가-힣]{2,4}", head[-1].strip()) and len(head) > 1:
        head = head[:-1]
    course = "".join(s.strip() for s in head) or None
    return course, room, building


def read_timetable_image(data: bytes) -> OcrResult:
    """이미지 바이트 → 수업 칸 미리보기."""
    cv2, np = _import_cv()
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise OcrError("이미지를 열 수 없습니다.")
    reader = _reader()
    notes: list[str] = []

    lines = _line_mask(img, np)
    cols = _find_columns(lines, np)
    hour_lines = _find_hour_lines(lines, cols, np)
    pitch = (hour_lines[-1] - hour_lines[0]) / (len(hour_lines) - 1)
    with _reader_lock:  # EasyOCR(torch) 는 동시 호출에 안전하다고 보장되지 않는다
        start_hour = _hour_labels(img, cols, hour_lines, reader)
        days = _days(img, cols, hour_lines, reader, notes)

        out: list[OcrSlot] = []
        for col, day in zip(cols, days):
            for y0, y1 in _blocks_in_column(img, col, hour_lines[0], hour_lines[-1], int(pitch * 0.2), np):
                crop = img[y0:y1, col[0] + 2: col[1] - 2]
                found = reader.readtext(crop, detail=1, paragraph=False)
                found.sort(key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
                text_lines = [t.strip() for _box, t, conf in found if t.strip() and conf >= 0.1]
                course, room, building = _parse_block_text(text_lines)
                out.append(OcrSlot(
                    day=day,
                    start=_to_hhmm(y0, hour_lines[0], pitch, start_hour),
                    end=_to_hhmm(y1, hour_lines[0], pitch, start_hour),
                    building_text=building,
                    room_text=room,
                    course=course,
                ))
    out.sort(key=lambda s: (DAY_ORDER.index(s.day), s.start))
    if not out:
        notes.append("색이 칠해진 수업 칸을 찾지 못했습니다.")
    if any(not s.building_text for s in out):
        notes.append("강의실 표기가 없는 칸은 개인 일정일 수 있어 건물을 비워 두었습니다.")
    return OcrResult(slots=out, notes=notes)
