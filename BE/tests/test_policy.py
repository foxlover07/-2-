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
