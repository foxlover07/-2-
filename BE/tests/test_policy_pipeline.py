"""정책 수집(온통청년 API 형식 자동 감지·지역 코드·원본 재처리) + 주거 형태 분류 + 매물 관련 정책.

실제 API 는 부르지 않는다 (httpx.MockTransport). 응답 모양은 테스트용으로 만든 값이다 (실제 정책 아님).
"""

import json

import httpx
import pytest

import pipeline.collect_policies as cp
from app.policy import PolicyItem, classify_housing, related_policies_for
from pipeline.policy_report import classification_report

V2_PAGE = {"resultCode": 200, "result": {"pagging": {"totCount": 3, "pageNum": 1, "pageSize": 2}, "youthPolicyList": [
    {"plcyNo": "P1", "plcyNm": "테스트 청년 월세 지원", "lclsfNm": "주거", "mclsfNm": "전월세 및 주거급여 지원",
     "plcyKywdNm": "월세,주거지원", "plcySprtCn": "월 20만 원 (임차보증금 제외)", "zipCd": "11110,11140"},
    {"plcyNo": "P2", "plcyNm": "테스트 일자리 지원", "lclsfNm": "일자리", "mclsfNm": "취업", "zipCd": "26110"},
]}}
V2_PAGE2 = {"result": {"pagging": {"totCount": 3}, "youthPolicyList": [
    {"plcyNo": "P3", "plcyNm": "테스트 전세임대", "lclsfNm": "주거", "mclsfNm": "주택 및 거주지",
     "rgtrUpInstCdNm": "국토교통부"},
]}}


def test_parse_v2_nested_rows_and_total():
    text = json.dumps(V2_PAGE, ensure_ascii=False)
    rows = cp.parse_rows(text)
    assert [r["plcyNo"] for r in rows] == ["P1", "P2"] and cp.parse_total(text) == 3
    assert cp.parse_total("<r><totalCnt>42</totalCnt></r>") == 42


def test_region_from_codes_or_sido_org():
    assert cp.region_name({"zipCd": "11110,11140"}) == "서울"
    assert cp.region_name({"zipCd": ",".join(f"{c}110" for c in cp.SIDO_CODES)}) == "전국"
    assert cp.region_name({"rgtrUpInstCdNm": "서울특별시"}) == "서울특별시"
    assert cp.region_name({"rgtrUpInstCdNm": "국토교통부"}) is None  # 중앙부처 = 지역 표기 없음 (전국처럼 통과)


def test_choose_format_falls_back_to_v1(monkeypatch):
    monkeypatch.setattr(cp, "_read", lambda k: None)
    v1_xml = "<youthPolicyList><youthPolicy><bizId>R1</bizId><polyBizSjnm>테스트 기숙사</polyBizSjnm></youthPolicy></youthPolicyList>"

    def handler(req: httpx.Request) -> httpx.Response:
        if "getPlcy" in str(req.url):
            return httpx.Response(200, json={"resultCode": 401, "resultMessage": "인증 실패"})
        assert req.url.params["openApiVlak"] == "KEY"
        return httpx.Response(200, text=v1_xml)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        cfg, r = cp.choose_format(c, "KEY", 100)
    assert cfg["format"] == "v1" and cp.parse_rows(r.text)[0]["bizId"] == "R1"


def test_choose_format_reports_both_failures_without_key(monkeypatch):
    monkeypatch.setattr(cp, "_read", lambda k: None)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="SECRETKEY 오류"))) as c:
        with pytest.raises(SystemExit) as e:
            cp.choose_format(c, "SECRETKEY", 10)
    assert "v2:" in str(e.value) and "v1:" in str(e.value) and "SECRETKEY" not in str(e.value)


def test_fetch_page_retries_5xx(monkeypatch):
    monkeypatch.setattr(cp.time, "sleep", lambda s: None)
    monkeypatch.setattr(cp, "_read", lambda k: None)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(500) if len(calls) < 3 else httpx.Response(200, json=V2_PAGE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        r = cp.fetch_page(c, cp._config("v2"), "K", 1, 2)
    assert r.status_code == 200 and len(calls) == 3


def test_from_raw_normalizes_filters_housing_and_reports(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "page_1.json").write_text(json.dumps(V2_PAGE, ensure_ascii=False), encoding="utf-8")
    (raw / "page_2.json").write_text(json.dumps(V2_PAGE2, ensure_ascii=False), encoding="utf-8")
    rep = cp.from_raw(raw, out_dir=tmp_path / "staging")
    assert rep["rows"] == 3 and rep["housing"] == 2  # 일자리 정책은 뺀다
    items = json.loads((tmp_path / "staging" / f"policies_{rep['ts']}.json").read_text(encoding="utf-8"))["items"]
    assert {i["policy_id"]: i["region"] for i in items} == {"P1": "서울", "P3": None}
    assert rep["classification"]["by_type"] == {"월세": 1, "공공임대": 1}


# ---------------------------------------------------------------- 분류

def T(name, **kw):
    return classify_housing(PolicyItem(policy_id=name, name=name, **kw))


def test_classify_ignores_generic_and_exclusion_phrases():
    # 중분류 '전월세' 는 형태를 가리지 않고, 지원 내용의 '보증금 제외' 는 보증금 근거가 아니다
    it = T("테스트 청년월세", subcategory="전월세 및 주거급여 지원", support="월 20만 원, 임차보증금 제외")
    assert it.housing_types == ["월세"] and it.housing_evidence == ["이름: '월세'"]
    assert T("테스트 전세임대주택").housing_types == ["공공임대"]  # 전세임대는 전세가 아니라 공공임대
    assert T("테스트 주거 상담").housing_types == []                  # 미분류 = 일반 주거 지원
    assert T("테스트", subcategory="기숙사").housing_types == ["기숙사"]
    assert T("수작업", housing_types=["전세"]).housing_types == ["전세"]  # 이미 있으면 유지


def test_classification_report_counts():
    rep = classification_report([T("월세 A"), T("전세보증금반환"), T("상담")])
    assert rep["by_type"] == {"월세": 1, "전세": 1, "보증금": 1} and rep["unclassified_count"] == 1
    assert rep["multi_count"] == 1


# ---------------------------------------------------------------- 매물 관련 정책

class P:
    def __init__(self, rent, deposit=500, address="서울특별시 서대문구 대현동 1-1"):
        self.property_id, self.monthly_rent_manwon, self.deposit_manwon, self.address = "X", rent, deposit, address


ITEMS = (
    T("서울 청년월세", region="서울"), T("부산 청년월세", region="부산"), T("전국 전세대출", region="전국"),
    T("반환보증", keywords=["보증금"]), T("행복주택"), T("주거 상담"),
)


def test_related_policies_by_rent_kind_and_region():
    r = related_policies_for(P(rent=50), items=ITEMS)
    assert r.rent_kind == "월세" and r.regions[:2] == ["서울특별시", "서대문구"]
    assert [i.name for i in r.items] == ["서울 청년월세", "반환보증"]  # 부산·전세·공공임대·미분류 제외, 지역 정책 먼저
    j = related_policies_for(P(rent=0, deposit=10000), items=ITEMS)
    assert j.rent_kind == "전세" and [i.name for i in j.items] == ["전국 전세대출", "반환보증"]
    assert related_policies_for(P(rent=None, deposit=None), items=ITEMS).available is False
    assert related_policies_for(P(rent=50), items=()).available is False
    text = json.dumps(r.model_dump(), ensure_ascii=False)
    assert not any(w in text for w in ("대상자입니다", "받을 수 있습니다"))


def test_related_policies_api():
    from fastapi.testclient import TestClient

    from app.data import load_properties
    from app.main import app

    pid = load_properties()[0].property_id
    b = TestClient(app).get(f"/market/related-policies/{pid}").json()
    assert b["rent_kind"] == "월세" and all(set(i["housing_types"]) & {"월세", "보증금"} for i in b["items"])
    assert TestClient(app).get("/market/related-policies/NOPE").status_code == 404
