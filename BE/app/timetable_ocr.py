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
# 한 줄에 과목명이 붙은 강의실 표기: 줄 끝 '건물 글자 + (알파벳)숫자 + (호)' (예: '…교양관609호', '… 정보통신관 B101호')
_ROOM_TAIL_RE = re.compile(r"([가-힣A-Za-z][가-힣A-Za-z()\s]*?[가-힣A-Za-z])\s*[A-Za-z]?\d{2,4}[\w-]*\s*(?:호|호실)?$")
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
    # 보통 열 너비: 넓은 칸들의 중앙값. 세로선이 긴 수업 칸에 가려지면 두 열이 한 칸(2배 너비)으로 보이므로
    # 보통 너비의 정수배인 칸은 나눈다 (2026-11-16 과기대 캡처: 수요일 선이 가려짐).
    # 왼쪽 시각 눈금 열·얇은 틈은 보통 너비에 못 미쳐 빠진다
    wide = sorted(b - a for a, b in spans if b - a >= widest * 0.4)
    base = wide[len(wide) // 2]
    cols: list[tuple[int, int]] = []
    for a, b in spans:
        k = round((b - a) / base)
        if k >= 1 and abs((b - a) - k * base) <= base * 0.12 * k:
            step = (b - a) / k
            cols += [(round(a + i * step), round(a + (i + 1) * step)) for i in range(k)]
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
        # 과목명·건물·호수가 한 줄로 붙어 읽힌 경우 (예: '전산수학기정운외T 교양관609호', '이산수학(영강 정보통신관 B101호').
        # 줄 끝의 호수 앞 글자를 건물 표기로 쓴다. 표준 이름 맞추기는 서버 검증(tools.match_building_text)이 한다
        for i in range(len(lines) - 1, -1, -1):
            m = _ROOM_TAIL_RE.search(lines[i].strip())
            if m:
                head = lines[:i]
                course = "".join(s.strip() for s in head) or None
                return course, lines[i].strip(), m.group(1).strip()
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


# ---------------------------------------------------------------- OpenCV 칸 위치 + AI 글자 (2026-11-16 A안)
# 배포 서버(무료, 메모리 512MB)에는 EasyOCR(torch)를 못 넣지만 OpenCV 는 넣을 수 있다.
# AI(Gemini)는 칸 시각을 30분·1시간 단위로 어림해서 75분 수업 등을 틀리게 읽는다 (11-16 실측).
# → 칸의 위치·시각은 OpenCV 격자로 재고, 과목·강의실 글자와 요일만 AI 결과에서 가져와 짝짓는다.
#   첫 가로선의 시각(몇 시)과 첫 열의 요일은 AI 칸들과 가장 잘 맞는 값으로 정한다 (EasyOCR 로 읽던 부분).

_MAX_COST = 240  # 칸 하나가 맞지 않을 때의 벌점 (분)


def grid_blocks(data: bytes) -> list[list[tuple[float, float]]]:
    """OpenCV 만으로 열마다 색칠된 칸의 (시작, 끝) — 첫 가로선부터 몇 시간 뒤인지 (실수). 글자는 읽지 않는다."""
    cv2, np = _import_cv()
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise OcrError("이미지를 열 수 없습니다.")
    lines = _line_mask(img, np)
    cols = _find_columns(lines, np)
    hour_lines = _find_hour_lines(lines, cols, np)
    first = hour_lines[0]
    pitch = (hour_lines[-1] - first) / (len(hour_lines) - 1)
    return [[((y0 - first) / pitch, (y1 - first) / pitch)
             for y0, y1 in _blocks_in_column(img, col, first, hour_lines[-1], int(pitch * 0.2), np)]
            for col in cols]


def _hm(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _snap(minutes: float) -> int:
    return max(0, min(round(minutes / _SNAP_MIN) * _SNAP_MIN, 23 * 60 + 55))


def align_with_grid(blocks: list[list[tuple[float, float]]], slots: list[dict]) -> tuple[list[dict], list[str]]:
    """AI 칸(요일·대략 시각·글자) → OpenCV 칸 시각으로 바꾼 칸 목록 + 안내.

    slots: [{day, start, end, building_text, course}] (AI 결과). 맞출 수 없으면 AI 시각 그대로 두고 안내한다.
    """
    notes: list[str] = []
    ai = [s for s in slots if s.get("day") in DAY_ORDER and re.fullmatch(r"\d{1,2}:\d{2}", s.get("start", ""))
          and re.fullmatch(r"\d{1,2}:\d{2}", s.get("end", ""))]
    n = len(blocks)
    if not ai or n == 0 or not any(blocks):
        return slots, ["칸 위치를 찾지 못해 AI 가 읽은 시각을 그대로 썼습니다. 시간을 확인해 주세요."]

    def block_min(c: int, b: tuple[float, float], start_hour: int) -> tuple[int, int]:
        start, end = _snap((start_hour + b[0]) * 60), _snap((start_hour + b[1]) * 60)
        # 칸 테두리·글자 때문에 시작이 몇 픽셀 밀린다 (15:05, 15:10). 수업 시작은 정각·30분이 대부분이라
        # 10분 이내면 맞춘다. 끝 시각(:15·:45 로 끝나는 75분 수업 등)은 건드리지 않는다
        near = round(start / 30) * 30
        if abs(start - near) <= 10:
            start = near
        return start, end

    # 첫 열의 요일(d0)과 첫 가로선 시각(h0): AI 칸과의 시작·끝 차이 합이 가장 작은 값
    best: tuple[float, int, int] | None = None
    for d0 in range(0, len(DAY_ORDER) - n + 1):
        for h0 in range(5, 15):
            cost = 0.0
            for s in ai:
                c = DAY_ORDER.index(s["day"]) - d0
                if not 0 <= c < n or not blocks[c]:
                    cost += _MAX_COST
                    continue
                a, e = _hm(s["start"]), _hm(s["end"])
                cost += min(min(abs(bs - a) + abs(be - e), _MAX_COST)
                            for bs, be in (block_min(c, b, h0) for b in blocks[c]))
            if best is None or cost < best[0]:
                best = (cost, d0, h0)
    assert best is not None
    cost, d0, h0 = best
    if cost / len(ai) > 90:  # 평균 1시간 반 넘게 어긋나면 격자를 믿지 않는다
        return slots, ["칸 위치와 AI 결과가 맞지 않아 AI 가 읽은 시각을 그대로 썼습니다. 시간을 확인해 주세요."]

    out: list[dict] = []
    used: set[int] = set()
    for c, col in enumerate(blocks):
        day = DAY_ORDER[d0 + c]
        cand = [(i, s) for i, s in enumerate(ai) if s["day"] == day]
        if len(cand) == len(col):
            # 칸 수가 같으면 시간 순서대로 짝짓는다 (AI 시각이 크게 어긋나도 순서는 맞다, 11-16 과기대 캡처)
            for b, (i, src) in zip(col, sorted(cand, key=lambda x: _hm(x[1]["start"]))):
                bs, be = block_min(c, b, h0)
                used.add(i)
                out.append({"day": day, "start": f"{bs // 60:02d}:{bs % 60:02d}", "end": f"{be // 60:02d}:{be % 60:02d}",
                            "building_text": src.get("building_text", ""), "course": src.get("course")})
            continue
        for b in col:
            bs, be = block_min(c, b, h0)
            # 겹치는 시간이 가장 긴 AI 칸 (없으면 가장 가까운 칸, 2시간 넘게 떨어지면 글자 없음)
            def score(item):
                a, e = _hm(item[1]["start"]), _hm(item[1]["end"])
                overlap = min(be, e) - max(bs, a)
                return (overlap, -(abs(bs - a) + abs(be - e)))
            pick = max(cand, key=score, default=None)
            if pick is not None and score(pick)[0] < 0 and -score(pick)[1] > 120:
                pick = None
            if pick is not None:
                used.add(pick[0])
            src = pick[1] if pick else {}
            out.append({"day": day, "start": f"{bs // 60:02d}:{bs % 60:02d}", "end": f"{be // 60:02d}:{be % 60:02d}",
                        "building_text": src.get("building_text", ""), "course": src.get("course")})
    missed = [s for i, s in enumerate(ai) if i not in used]
    if missed:
        notes.append(f"칸 위치를 찾지 못한 {len(missed)}칸은 AI 가 읽은 시각을 그대로 썼습니다. 시간을 확인해 주세요.")
        out += [dict(s) for s in missed]
    out.sort(key=lambda s: (DAY_ORDER.index(s["day"]), s["start"]))
    notes.append("칸의 요일·시각은 시간표 격자 위치로 계산했고, 과목·강의실 글자는 AI 로 읽었습니다.")
    return out, notes
