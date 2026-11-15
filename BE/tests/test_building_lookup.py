"""목록에 없는 건물 → 카카오 장소 검색으로 찾아 기준표에 추가 (app.building_lookup). 실제 카카오는 부르지 않는다.

장소 응답은 테스트용으로 만든 값이다 (실제 좌표 아님).
"""

import pytest

from app import building_lookup as bl
from app import building_walks
from app import frequent_places as fp

KU = "고려대학교"


class FakeKakao:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def search_places_near(self, **kw):
        self.calls.append(kw["query"])
        return {"documents": self.docs.get(kw["query"], [])}


def doc(name, pid="1", x="127.03", y="37.59"):
    return {"place_name": name, "id": pid, "x": x, "y": y}


@pytest.fixture
def st(tmp_path, monkeypatch):
    """임시 기준표 폴더 + 메모리 기록. 실제 DB/ref 를 건드리지 않는다."""
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "korea_buildings.csv").write_text(
        ",".join(bl.FIELDS) + "\nKU_A,고려대학교,문과대학 서관,building_poi,lecture,고려대학교 문과대학서관,9,37.59,127.03,"
        "kakao_keyword_search,2026-11-09,false,\n", encoding="utf-8")
    monkeypatch.setattr(fp, "REF_DIR", ref)
    fp.campus_center.cache_clear()
    building_walks._catalog.cache_clear()
    s = bl._State.__new__(bl._State)
    s.auto_path, s.log_path, s.log = ref / bl.AUTO_FILE, None, {}
    s.budget = fp.CallBudget(None, 5)
    s.client = None
    yield s
    fp.campus_center.cache_clear()
    building_walks._catalog.cache_clear()


def test_adds_campus_building_and_does_not_search_again(st):
    st.client = FakeKakao({"고려대학교 정보통신관": [
        doc("고려대학교 정보통신관 행정실", "2"),     # 건물이 아닌 장소는 버림
        doc("고려대학교 정보통신관", "3", "127.031", "37.588"),
    ]})
    lk = bl.find_building(KU, "정보통신관", st)
    assert lk.status == "added" and lk.name == "정보통신관"
    assert "정보통신관" in building_walks.catalog_names(KU)  # 기준표에 들어가 고르고 그때 계산할 수 있다
    row = (st.auto_path.read_text(encoding="utf-8")).splitlines()[1]
    assert "kakao_keyword_auto" in row and ",false," in row and "출입구 아님" in row
    assert bl.find_building(KU, "정보통신관", st).status == "known" and len(st.client.calls) == 1


def test_rejects_other_places_and_records_miss(st):
    st.client = FakeKakao({"고려대학교 정보관": [doc("서울시립 정보관"), doc("고려대학교 정보대학 학과사무실")]})
    assert bl.find_building(KU, "정보관", st).status == "not_found"
    assert bl.find_building(KU, "정보관", st).status == "not_found" and len(st.client.calls) == 1  # 다시 안 부름
    assert not st.auto_path.exists()


def test_strip_university_prefix_variants():
    assert bl._strip_uni("고려대학교 서울캠퍼스 SK미래관", KU) == "SK미래관"
    assert bl._strip_uni("고려대학교서울캠퍼스 법학관신관", KU) == "법학관신관"
    assert bl._strip_uni("서울시립대학교 정보관", KU) is None


def test_limit_and_unavailable(st):
    assert bl.find_building(KU, "정보통신관", st).status == "unavailable"  # 키 없음·off
    st.client = FakeKakao({})
    st.budget = fp.CallBudget(None, 0)
    assert bl.find_building(KU, "정보통신관", st).status == "limit"


def test_timetable_check_uses_lookup(st, monkeypatch):
    from app.agent import tools
    from app.agent.schemas import SlotRead, TimetableRead

    st.client = FakeKakao({"고려대학교 정보통신관": [doc("고려대학교 정보통신관", "3")]})
    monkeypatch.setattr(bl, "state", lambda: st)
    monkeypatch.setattr(tools, "usable_buildings", lambda u: building_walks.catalog_names(u))
    assert tools.room_building_name("이산수학(영강 정보통신관 B101호") == "정보통신관"
    read = TimetableRead(slots=[SlotRead(day="화", start="16:30", end="17:45",
                                         building_text="이산수학(영강 정보통신관 B101호", course=None)], notes=[])
    s = tools.check_timetable_read(read, KU)[0]
    assert s.building == "정보통신관" and "카카오 지도에서 찾아 새로 추가" in s.issue
