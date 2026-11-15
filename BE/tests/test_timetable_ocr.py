"""시간표 캡처 OCR (app.timetable_ocr, POST /timetable/ocr).

- 글자 해석·시각 변환·API 응답은 OCR 없이 테스트한다.
- 실제 캡처 테스트는 OpenCV·EasyOCR 과 fixture 이미지가 있을 때만 돈다 (모델 로딩에 수십 초).
  fixture: tests/fixtures/everytime_ewha_2026_2.png (에브리타임 이대 시간표, 사용자 제공 2026-11-15, 커밋하지 않음)
"""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import timetable_ocr
from app.timetable_ocr import OcrResult, OcrSlot, _parse_block_text, _to_hhmm

EWHA = "이화여자대학교"
FIXTURE = Path(__file__).parent / "fixtures" / "everytime_ewha_2026_2.png"

# 사용자 확인 정답 (요일·시각·강의실). 요일 줄이 잘린 캡처라 왼쪽 열부터 월~토로 가정한다.
EXPECTED = [
    ("월", "15:30", "18:15", "캠"),
    ("화", "11:00", "12:15", "학"),
    ("화", "17:00", "18:15", "캠"),
    ("화", "18:30", "19:45", "포"),
    ("수", "09:30", "12:15", "경영"),
    ("수", "15:30", "16:45", "포"),
    ("수", "17:00", "18:15", "캠"),
    ("목", "10:00", "11:00", ""),   # 랩미팅 (개인 일정, 강의실 없음)
    ("금", "10:00", "10:30", ""),   # ㅊㅍ
    ("금", "11:00", "12:15", "학"),
    ("금", "12:30", "13:45", "포"),
    ("금", "15:30", "16:45", "캠"),
    ("금", "17:00", "18:15", "캠"),
    ("토", "10:00", "14:00", ""),   # 봉사활동
]


# ---------------------------------------------------------------- 글자 해석

@pytest.mark.parametrize(
    ("lines", "course", "room", "building"),
    [
        (["강화학습", "김수경", "캠B134"], "강화학습", "캠B134", "캠"),
        (["기독교와세", "계", "박지은", "학109"], "기독교와세계", "학109", "학"),
        (["글로벌커리", "어개발과기", "업가정신", "송민영", "경영313"], "글로벌커리어개발과기업가정신", "경영313", "경영"),
        (["자료구조", "ECC B142"], "자료구조", "ECC B142", "ECC"),
        (["봉사활동"], "봉사활동", "", ""),       # 강의실 줄 없음 → 개인 일정
        (["랩미팅"], "랩미팅", "", ""),
        ([], None, "", ""),
        # 과목명·건물·호수가 한 줄로 붙어 읽힌 경우 (고려대 캡처, 2026-11-16)
        (["전산수학", "기정운외T 교양관609호"], "전산수학", "기정운외T 교양관609호", "기정운외T 교양관"),
        (["자료구조(영강", "정보통신관 604"], "자료구조(영강", "정보통신관 604", "정보통신관"),
        (["데이터과학과인공지능"], "데이터과학과인공지능", "", ""),
    ],
)
def test_parse_block_text(lines, course, room, building):
    assert _parse_block_text(lines) == (course, room, building)


def test_to_hhmm_snaps_to_5_minutes():
    # 첫 선(y=477)이 9시, 1시간 = 135px
    assert _to_hhmm(477, 477, 135, 9) == "09:00"
    assert _to_hhmm(545, 477, 135, 9) == "09:30"   # 67.5px → 30분
    assert _to_hhmm(1019, 477, 135, 9) == "13:00"  # 2px 어긋나도 5분 단위로 맞춤
    assert _to_hhmm(1962, 477, 135, 9) == "20:00"


# ---------------------------------------------------------------- API (OCR 결과를 대신 넣어 검증)

def _post(client, data: bytes = b"fake-image"):
    return client.post("/timetable/ocr", json={
        "university": EWHA, "image_base64": base64.b64encode(data).decode(), "mime_type": "image/png",
    }).json()


def test_ocr_endpoint_maps_rooms_to_buildings(monkeypatch):
    from app.main import app

    fake = OcrResult(slots=[
        OcrSlot("월", "15:30", "18:15", "캠", "캠B147", "시계열분석개론및실습"),
        OcrSlot("화", "11:00", "12:15", "학", "학109", "기독교와세계"),
        OcrSlot("화", "18:30", "19:45", "포", "포252", "선형대수학I"),
        OcrSlot("수", "09:30", "12:15", "경영", "경영313", "글로벌커리어개발과기업가정신"),
        OcrSlot("목", "10:00", "11:00", "", "", "랩미팅"),
    ], notes=["요일 줄이 보이지 않아 왼쪽 열부터 월~토요일로 가정했습니다. 요일을 확인해 주세요."])
    monkeypatch.setattr(timetable_ocr, "read_timetable_image", lambda data: fake)

    b = _post(TestClient(app))
    assert b["status"] == "ok" and len(b["slots"]) == 5
    s = b["slots"]
    assert [x["building"] for x in s[:3]] == ["ECC", "학관", "이화·포스코관"]  # 에브리타임 앞 글자 → 표준 이름
    assert all(x["issue"] is None for x in s[:3])
    assert s[0]["building_text"] == "캠B147" and s[0]["course"] == "시계열분석개론및실습"
    assert s[3]["building"] == "이화신세계관" and s[3]["issue"] is None  # '경영' = 경영관(이화신세계관), 2026-11-16 사용자 확인
    assert s[4]["building"] is None and s[4]["issue"] == "건물 표기 없음"  # 개인 일정
    assert "요일" in b["notes"][0] and "1칸은 확인이 필요" in b["message"]


def test_ocr_endpoint_unavailable_and_errors(monkeypatch):
    from app.main import app

    client = TestClient(app)

    def unavailable(data):
        raise timetable_ocr.OcrUnavailable("없음")

    monkeypatch.setattr(timetable_ocr, "read_timetable_image", unavailable)
    assert _post(client)["status"] == "ocr_unavailable"

    def bad(data):
        raise timetable_ocr.OcrError("세로 격자선을 찾지 못했습니다.")

    monkeypatch.setattr(timetable_ocr, "read_timetable_image", bad)
    b = _post(client)
    assert b["status"] == "ocr_error" and "세로 격자선" in b["message"]

    b = client.post("/timetable/ocr", json={"university": EWHA, "image_base64": "@@not-base64@@",
                                            "mime_type": "image/png"}).json()
    assert b["status"] == "ocr_error"


# ---------------------------------------------------------------- 실제 캡처

@pytest.mark.skipif(not timetable_ocr.is_available() or not FIXTURE.exists(),
                    reason="OpenCV·EasyOCR 또는 fixture 이미지 없음")
def test_real_everytime_capture():
    result = timetable_ocr.read_timetable_image(FIXTURE.read_bytes())
    got = [(s.day, s.start, s.end, s.building_text) for s in result.slots]
    assert got == EXPECTED
    assert any("요일 줄이 보이지 않아" in n for n in result.notes)


def test_match_building_text_exact_suffix_and_fuzzy(monkeypatch):
    from app.agent import tools

    known = ["우당교양관", "정운오IT교양관", "문과대학 서관", "우정정보관"]
    monkeypatch.setattr(tools, "usable_buildings", lambda u: known)
    monkeypatch.setitem(tools.ALIASES, "교양관", "우당교양관")
    monkeypatch.setitem(tools.ALIASES, "서관", "문과대학 서관")
    m = tools.match_building_text
    assert m("우당교양관 301", "고려대학교") == ("우당교양관", False)
    assert m("교양관609호", "고려대학교") == ("우당교양관", False)             # 줄임말
    assert m("현대시인론 문과대학 서관 210", "고려대학교") == ("문과대학 서관", False)  # 앞에 과목명
    # OCR 오타: 긴 뒷부분을 먼저 보므로 줄임말 '교양관'(우당교양관)이 아니라 정운오IT교양관으로 추정
    assert m("기정운외T 교양관609호", "고려대학교") == ("정운오IT교양관", True)
    assert m("정보통신관 604", "고려대학교") == (None, False)                  # 데이터에 없는 건물은 만들지 않는다
    assert m("관 1", "고려대학교") == (None, False)
    # OCR 이 앞에 기호를 붙여 읽어도 2글자 줄임말 전체 일치는 맞춘다
    for t in ("'서관 315호", "(서관 315호", "서관315호"):
        assert m(t, "고려대학교") == ("문과대학 서관", False)
