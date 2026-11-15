"""주거 정책 안내 (app.policy, pipeline.collect_policies) + 챗봇 policy 의도. 자격 판정이 없는지 확인한다.

정책 항목은 테스트용으로 만든 값이다 (실제 정책 아님). 실제 API 는 부르지 않는다.
"""

import uuid

import pytest

from app.agent.graph import build_graph
from app.agent.llm import ScriptedLLM
from app.agent.schemas import ProfileChanges, TurnExtraction
from app.policy import PolicyItem, search_policies
from pipeline.collect_policies import is_housing, normalize, parse_rows

ITEMS = (
    PolicyItem(policy_id="A", name="테스트 청년 월세 지원", summary="무주택 청년 월세 일부 지원", support="월 최대 20만 원",
               subcategory="전월세 및 주거급여 지원", agency="테스트부", region="전국", apply_url="https://example.org/a",
               fetched_at="2026-11-15T00:00:00+09:00"),
    PolicyItem(policy_id="B", name="테스트 전세 대출 이자 지원", summary="임차 보증금 대출 이자 일부 지원", region="서울"),
    PolicyItem(policy_id="C", name="테스트 연합기숙사", summary="대학생 기숙사 입사 지원", region="부산"),
)


def test_search_matches_synonyms_and_region():
    r = search_policies("월세 지원", items=ITEMS)
    assert r.available and r.items[0].policy_id == "A"
    r = search_policies("전세", items=ITEMS)
    assert r.items[0].policy_id == "B"  # '전세' → 보증금·임차 낱말도 찾음
    r = search_policies("기숙사", region="서울", items=ITEMS)
    assert [i.policy_id for i in r.items] != ["C"]  # 부산 정책은 서울 검색에서 빠짐
    none = search_policies("월세", items=())
    assert not none.available and "키 발급" in none.reason and none.official_links


def test_collector_parses_json_and_xml_and_filters_housing():
    js = '{"result":{"youthPolicyList":[{"plcyNo":"1","plcyNm":"청년 월세","lclsfNm":"주거","mclsfNm":"전월세 및 주거급여 지원",' \
         '"sprtTrgtMinAge":"19","sprtTrgtMaxAge":"34","plcyKywdNm":"월세,청년","aplyUrlAddr":"https://x"},' \
         '{"plcyNo":"2","plcyNm":"취업 지원","lclsfNm":"일자리"}]}}'
    rows = parse_rows(js)
    items = [normalize(r, "t") for r in rows]
    assert [i["name"] for i in items if is_housing(i)] == ["청년 월세"]
    assert items[0]["target_age"] == "19~34세" and items[0]["keywords"] == ["월세", "청년"]
    xml = "<youthPolicyList><youthPolicy><bizId>R1</bizId><polyBizSjnm>청년 전세 지원</polyBizSjnm>" \
          "<sporCn>보증금 대출</sporCn></youthPolicy></youthPolicyList>"
    x = [normalize(r, "t") for r in parse_rows(xml)]
    assert x[0]["policy_id"] == "R1" and x[0]["support"] == "보증금 대출" and is_housing(x[0])


def _chat(extractions, explanations=None):
    llm = ScriptedLLM(extractions, explanations)
    g = build_graph(lambda: llm)
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    return llm, (lambda m: g.invoke({"user_message": m, "session_id": "p", "fe_profile": None}, config=cfg))


def test_policy_intent_without_profile_and_no_eligibility_words(monkeypatch):
    import app.policy as pol

    monkeypatch.setattr(pol, "load_policies", lambda: ITEMS)
    llm, say = _chat([TurnExtraction(intent="policy", policy_query="월세 지원", changes=ProfileChanges())])
    s = say("청년 월세 지원 있어? 나도 받을 수 있어?")
    assert s["status"] == "ok" and not s.get("pending_required_fields")  # 프로필 필수 조건을 묻지 않음
    assert s["policy"]["items"][0]["policy_id"] == "A"
    text = s["response"]
    assert "테스트 청년 월세 지원" in text and "공식" in text
    assert not any(w in text for w in ("대상입니다", "받을 수 있습니다", "해당됩니다"))


def test_policy_unavailable_answers_with_links_only(monkeypatch):
    import app.policy as pol

    monkeypatch.setattr(pol, "load_policies", lambda: ())
    _, say = _chat([TurnExtraction(intent="policy", policy_query="월세", changes=ProfileChanges())])
    s = say("월세 지원 정책")
    assert "youthcenter.go.kr" in s["response"] and "키 발급" in s["response"]


def test_region_compare_apply_and_checklist(monkeypatch):
    """2026-11-15 예시 프롬프트: 지역(구 → 서울 포함)·비교(유불리 판단 없음)·신청 절차·계약 점검 목록."""
    from app.policy import compare_policies, contract_checklist, expand_regions, region_match

    assert expand_regions(["서대문구"]) == ["서울", "서대문구"]
    assert region_match("서울특별시", ["서대문구"]) is False and region_match("서울특별시", expand_regions(["서대문구"]))
    r = search_policies("주거 지원", regions=["서대문구"], items=ITEMS)
    assert "C" not in {i.policy_id for i in r.items} and any("구청" in n for n in r.notes)  # 부산 정책 빠짐

    c = compare_policies(["청년 월세 지원", "전세 대출", "없는 정책 이름"], items=ITEMS)
    assert [i.policy_id for i in c.items] == ["A", "B"] and c.missing_names == ["없는 정책 이름"]
    assert "판단하지 않아요" in c.notes[0]

    import app.policy as pol
    monkeypatch.setattr(pol, "load_policies", lambda: ITEMS)
    _, say = _chat([TurnExtraction(intent="policy", policy_query="월세 지원", policy_kind="apply"),
                    TurnExtraction(intent="policy", policy_kind="compare", policy_names=["월세 지원", "전세 대출"]),
                    TurnExtraction(intent="policy", policy_kind="eligibility", policy_query="월세"),
                    TurnExtraction(intent="policy", policy_kind="contract_safety")])
    a = say("월세 지원 신청 서류")["response"]
    assert "신청 방법: 공식 데이터에 표기 없음" in a
    b = say("뭐가 더 유리해?")["response"]
    assert "테스트 청년 월세 지원" in b and "테스트 전세 대출 이자 지원" in b and "유리" in b  # '판단하지 않는다'는 안내
    e = say("20살 소득 80만 원인데 받을 수 있어?")
    assert "판정하지 않아요" in e["response"] and e["current_profile"] is None  # 개인 정보는 저장하지 않음
    k = say("전세사기 안 당하려면?")
    assert k["policy"]["kind"] == "contract_safety" and "iros.go.kr" in k["response"] and "계약 당일" in k["response"]
    assert len(contract_checklist().checklist) == 3
    for t in (a, b, e["response"], k["response"]):
        assert not any(w in t for w in ("대상입니다", "받을 수 있습니다", "해당됩니다", "더 유리합니다"))


def test_curated_list_used_without_api_data(monkeypatch, tmp_path):
    """API 데이터가 없으면 DB/reference/policies_curated.json (공식 페이지 확인 임시 목록) 을 쓰고 출처를 그렇게 표시한다."""
    import app.policy as pol

    monkeypatch.setattr(pol, "REPO_ROOT", tmp_path)  # staging 없음
    (tmp_path / "DB" / "reference").mkdir(parents=True)
    real = pol.Path(__file__).resolve().parents[2] / "DB" / "reference" / "policies_curated.json"
    (tmp_path / "DB" / "reference" / "policies_curated.json").write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.delenv("POLICIES_PATH", raising=False)
    monkeypatch.setattr(pol, "_read", lambda k: None)
    pol.load_policies.cache_clear()
    try:
        items = pol.load_policies()
        assert len(items) >= 5 and all(i.ref_url and i.fetched_at for i in items)  # 항목마다 공식 페이지·확인일
        r = pol.search_policies("월세 지원")
        assert r.available and r.source == pol.CURATED_SOURCE and "월세" in r.items[0].name
        assert pol.search_policies("기숙사", regions=["부산"]).items  # 전국 정책은 지역 검색에도 남음
        assert all(i.region != "서울" for i in pol.search_policies("월세", regions=["부산"]).items)
        c = pol.compare_policies(["청년월세", "버팀목"])
        assert len(c.items) == 2 and not c.missing_names
    finally:
        pol.load_policies.cache_clear()


def test_policy_api_response_field(monkeypatch):
    from fastapi.testclient import TestClient

    import app.policy as pol
    from app.main import app
    from app.routers import agent as agent_router

    monkeypatch.setattr(pol, "load_policies", lambda: ITEMS)
    llm = ScriptedLLM([TurnExtraction(intent="policy", policy_query="기숙사", changes=ProfileChanges())], ["정책을 찾았어요."])
    monkeypatch.setattr(agent_router, "_provider_llm", lambda: llm)
    b = TestClient(app).post("/agent/chat", json={"session_id": "pol-1", "message": "기숙사 지원"}).json()
    assert b["status"] == "ok" and b["policy_results"]["items"][0]["policy_id"] == "C"
    assert b["recommendation_results"] is None


def test_contract_check_attaches_property_facts_without_verdict():
    from app.building_register import BuildingInfo
    from app.data import load_properties
    from app.market import MarketCompare
    import app.policy as pol

    p = load_properties()[0]
    m = MarketCompare(property_id=p.property_id, available=True, scope="동", n=12, listing_monthly_rent_manwon=55,
                      rent_median=50, deposit_median=1000, level="주변 실거래보다 높은 편")
    b = BuildingInfo(property_id=p.property_id, available=True, main_purpose="제2종근린생활시설", detail_purpose="다가구주택")
    c = pol.contract_check_for(p, market=m, building=b)
    facts = {i.key: i.fact for s in c.phases for i in s.items if i.key}
    assert "중앙값 50만 원" in facts["market"] and "높은 편" in facts["market"]
    assert "주용도가 근린생활시설" in facts["building"] and "다가구" in facts["multi_family"]
    # 주용도가 오피스텔(업무시설)이고 근생이 함께 있을 뿐이면 강한 안내 대신 호실 용도 확인 안내
    mixed = BuildingInfo(property_id=p.property_id, available=True, main_purpose="업무시설",
                         detail_purpose="업무시설(오피스텔,제1종근린생활시설)")
    f2 = {i.key: i.fact for s in pol.contract_check_for(p, market=m, building=mixed).phases for i in s.items if i.key}
    assert "주용도가 근린생활시설" not in f2["building"] and "전유부" in f2["building"]
    # 일반 목록 구조는 그대로 (항목 수·단계)
    assert [len(s.items) for s in c.phases] == [len(s["items"]) for s in pol.CONTRACT_CHECKLIST]
    text = " ".join(f or "" for f in facts.values())
    assert not any(w in text for w in ("안전합니다", "위험합니다", "대상자", "받을 수 있"))


def test_contract_check_without_data_says_so():
    from app.building_register import BuildingInfo
    from app.data import load_properties
    from app.market import MarketCompare
    import app.policy as pol

    p = load_properties()[0]
    c = pol.contract_check_for(p, market=MarketCompare(property_id=p.property_id, available=False, reason="거래 없음"),
                               building=BuildingInfo(property_id=p.property_id, available=False, reason="지번 없음"))
    facts = {i.key: i.fact for s in c.phases for i in s.items if i.key}
    assert "하지 못했어요" in facts["market"] and "찾지 못했어요" in facts["building"]
    assert facts["multi_family"] is None


def test_contract_check_api():
    from fastapi.testclient import TestClient

    from app.data import load_properties
    from app.main import app

    pid = load_properties()[0].property_id
    r = TestClient(app).get(f"/market/contract-check/{pid}")
    assert r.status_code == 200 and r.json()["phases"][0]["phase"] == "계약 전"
    assert TestClient(app).get("/market/contract-check/NOPE").status_code == 404
