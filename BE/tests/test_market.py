"""실거래 비교 (app.market) · 공공데이터 공통부 (app.publicdata) · 수집 정규화 (pipeline.collect_rent).

실거래 표본은 테스트용으로 만든 값이다 (실제 데이터 아님). 실제 API 는 부르지 않는다.
"""

from datetime import date

import pytest

from app.market import CompareConfig, Tx, address_parts, compare_property, tx_types_for
from app.models import Property
from app.publicdata import PublicDataError, check_error, service_key, xml_items
from pipeline.collect_rent import months_back, normalize


def prop(**kw) -> Property:
    base = dict(property_id="T1", university="이화여자대학교", property_type="원룸", address="서울특별시 서대문구 대현동 53-33",
                latitude=None, longitude=None, deposit_manwon=1000, monthly_rent_manwon=60, maintenance_manwon=8,
                area_m2=18, source_url=None, listing_checked_at=None,
                spot1_name=None, spot1_walk_min=None, spot2_name=None, spot2_walk_min=None,
                spot3_name=None, spot3_walk_min=None, spot4_name=None, spot4_walk_min=None, monthly_housing_cost_manwon=None)
    return Property(**(base | kw))


def tx(rent, *, dong="대현동", t="sh", area=18.0, dep=1000.0, d=date(2026, 9, 1), sgg="11410"):
    return Tx(t, sgg, dong, area, dep, rent, d, "다가구", "2026-11-15T03:00:00+09:00")


# ---------------------------------------------------------------- 주소·유형

def test_address_parts_and_types():
    assert address_parts("서울특별시 성북구 안암동5가 103-1") == ("11290", "안암동5가")
    assert address_parts("서울특별시 서대문구 대현동 53-33") == ("11410", "대현동")
    assert address_parts("서울특별시 강남구 역삼동 1") == (None, "역삼동")
    assert tx_types_for("오피스텔") == ["offi"] and tx_types_for("원룸") == ["sh", "rh"]


# ---------------------------------------------------------------- 비교

def test_compare_percentile_level_and_filters():
    rents = [50, 52, 54, 55, 56, 58, 60, 62, 64, 66, 70]  # 11건, 매물 월세 60
    noise = [
        tx(10, t="offi"),                      # 유형 다름
        tx(10, area=40),                       # 면적 ±30% 밖
        tx(10, dep=5000),                      # 보증금 범위 밖
        tx(0),                                 # 전세 (월세 0)
        tx(10, d=date(2025, 1, 1)),            # 12개월 이전
        tx(10, sgg="11440", dong="대현동"),     # 다른 구
    ]
    r = compare_property(prop(), tuple([tx(x) for x in rents] + noise))
    assert r.available and r.scope == "동" and r.n == 11
    assert r.rent_median == 58 and r.listing_percentile == pytest.approx((6 + 0.5) / 11 * 100, abs=0.1)
    assert r.level == "주변 실거래와 비슷한 편"
    assert r.criteria["area_m2"] == [12.6, 23.4] and r.criteria["deposit_manwon"] == [500.0, 1500.0]
    assert any("관리비" in n for n in r.notes) and r.listing_maintenance_manwon == 8


def test_compare_widens_to_gu_and_skips_level_when_few():
    few_same_dong = [tx(60), tx(61)]
    other_dong = [tx(70, dong="신촌동") for _ in range(9)]
    r = compare_property(prop(), tuple(few_same_dong + other_dong))
    assert r.scope == "구" and r.n == 11 and r.criteria["umd_nm"] is None
    assert r.level == "주변 실거래보다 낮은 편"  # 60 < 대부분 70
    small = compare_property(prop(), tuple([tx(60), tx(61), tx(62), tx(63)]), CompareConfig(min_n_judge=10, min_n_show=3))
    assert small.available and small.level is None and any("판단하지 않았습니다" in n for n in small.notes)
    none = compare_property(prop(), (tx(60),))
    assert not none.available and "1건" in none.reason


def test_compare_unavailable_cases():
    assert "수집 전" in compare_property(prop(), ()).reason
    assert "구에 있는 매물이 아닙니다" in compare_property(prop(address="서울특별시 강남구 역삼동 1"), (tx(60),)).reason
    assert "월세 또는 면적" in compare_property(prop(monthly_rent_manwon=None), (tx(60),)).reason


# ---------------------------------------------------------------- 공공데이터 공통부

def test_service_key_decodes_encoding_key(monkeypatch):
    import app.publicdata as pd
    monkeypatch.setattr(pd, "_read", lambda n: "abc%2Bdef%3D%3D")
    assert service_key() == "abc+def=="
    monkeypatch.setattr(pd, "_read", lambda n: "abc+def==")
    assert service_key() == "abc+def=="
    monkeypatch.setattr(pd, "_read", lambda n: None)
    with pytest.raises(PublicDataError) as e:
        service_key()
    assert e.value.kind == "no_key"


def test_check_error_kinds():
    with pytest.raises(PublicDataError) as e:
        check_error("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>")
    assert e.value.kind == "not_registered"
    with pytest.raises(PublicDataError) as e:
        check_error('{"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"}}}')
    assert e.value.kind == "limit"
    check_error("<response><header><resultCode>000</resultCode></header></response>")  # 정상은 통과


def test_xml_items_and_normalize():
    xml = ("<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header><body><items>"
           "<item><deposit>3,500</deposit><monthlyRent>30</monthlyRent><dealYear>2026</dealYear><dealMonth>10</dealMonth>"
           "<dealDay>3</dealDay><houseType>다가구</houseType><totalFloorAr>33</totalFloorAr><umdNm>홍제동</umdNm><sggCd>11410</sggCd>"
           "<buildYear> </buildYear></item></items><totalCount>1</totalCount></body></response>")
    items, total, code = xml_items(xml)
    assert total == 1 and code == "000" and items[0]["buildYear"] == ""
    row = normalize("sh", items[0], "2026-11-15T00:00:00+09:00", "raw/x.xml")
    assert row["deposit_manwon"] == 3500 and row["monthly_rent_manwon"] == 30 and row["contract_date"] == "2026-10-03"
    assert row["area_m2"] == 33 and row["rent_kind"] == "월세" and "계약면적" in row["area_kind"]
    jeonse = normalize("offi", {"deposit": "20,000", "monthlyRent": "0", "excluUseAr": "16.8"}, "t", "r")
    assert jeonse["rent_kind"] == "전세" and jeonse["contract_date"] == "" and "전용면적" in jeonse["area_kind"]


def test_months_back():
    assert months_back("202602", 4) == ["202511", "202512", "202601", "202602"]


# ---------------------------------------------------------------- 생활 편의시설 (app.amenities)

from app.amenities import Store, amenities_for, distance_m  # noqa: E402


def test_distance_m_matches_known_scale():
    # 위도 0.001도 ≈ 111m
    assert distance_m(37.5, 127.0, 37.501, 127.0) == pytest.approx(111.2, abs=0.5)


def test_amenities_counts_nearest_and_unavailable():
    lat, lon = 37.5590, 126.9466
    stores = (
        Store("convenience", "가게A", "주소A", lat + 0.0009, lon, "202609"),   # 약 100m
        Store("convenience", "가게B", "주소B", lat + 0.0036, lon, "202609"),   # 약 400m
        Store("convenience", "가게C", "주소C", lat + 0.0090, lon, "202609"),   # 약 1km (범위 밖)
        Store("bar", "주점A", "주소D", lat, lon + 0.002, "202609"),            # 약 176m
    )
    r = amenities_for(prop(latitude=lat, longitude=lon), stores)
    by = {c.category: c for c in r.categories}
    assert r.available and by["convenience"].nearest.name == "가게A" and 95 <= by["convenience"].nearest.distance_m <= 105
    assert by["convenience"].counts == {"300": 1, "500": 2}
    assert by["laundry"].nearest is None and by["laundry"].counts == {"300": 0, "500": 0}
    assert by["bar"].group == "context" and any("소음" in n for n in r.notes)
    assert not amenities_for(prop(), stores).available  # 좌표 없음
    assert "수집 전" in amenities_for(prop(latitude=lat, longitude=lon), ()).reason


def test_market_api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.amenities as am
    import app.market as mk
    from app.main import app

    monkeypatch.setattr(mk, "load_transactions", lambda: tuple(tx(x) for x in range(50, 62)))
    monkeypatch.setattr(am, "load_stores", lambda: ())
    cl = TestClient(app)
    b = cl.get("/market/compare/P001").json()
    assert b["property_id"] == "P001" and "source" in b
    assert cl.get("/market/amenities/P001").json()["available"] is False
    assert cl.get("/market/compare/NOPE").status_code == 404


def test_tx_sources_picks_latest_file_per_type(tmp_path, monkeypatch):
    import json as _json

    import app.market as mk

    staging = tmp_path / "DB" / "staging"
    staging.mkdir(parents=True)
    for name, by_type in [("rent_tx_20261101T000000", {"sh": 5, "offi": 3, "rh": 0}),
                          ("rent_tx_20261110T000000", {"sh": 7}),
                          ("rent_tx_20261115T000000", {"rh": 4})]:
        (staging / f"{name}.csv").write_text("x", encoding="utf-8")
        (staging / f"{name}.report.json").write_text(_json.dumps({"by_type": by_type}), encoding="utf-8")
    monkeypatch.setattr(mk, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mk, "_read", lambda n: None)
    got = [(p.name, sorted(t)) for p, t in mk._tx_sources()]
    # 최신부터: rh(15일) → sh(10일) → offi 만 1일 파일에서 (sh 는 이미 더 최신 파일이 있음)
    assert got == [("rent_tx_20261115T000000.csv", ["rh"]), ("rent_tx_20261110T000000.csv", ["sh"]),
                   ("rent_tx_20261101T000000.csv", ["offi"])]


# ---------------------------------------------------------------- 건축물대장 (app.building_register, pipeline.collect_building)

def _title(**kw):
    base = {"address": "서울특별시 서대문구 대현동 53-33", "match_status": "matched", "bld_nm": "이화랑", "main_purps": "공동주택",
            "etc_purps": "도시형생활주택", "use_apr_day": "20121004", "grnd_flr_cnt": "5", "ugrnd_flr_cnt": "0",
            "elevator_cnt": "0", "parking_cnt": "5", "hhld_cnt": "18", "strct": "철근콘크리트구조", "tot_area_m2": "436.16",
            "fetched_at": "2026-11-15T12:44:12+09:00"}
    return base | kw


def test_building_for_matched_and_unavailable():
    from app.building_register import address_key, building_for

    key = address_key("서울특별시 서대문구 대현동 53-33")
    assert key == "서대문구|대현동|0|53|33" and address_key("서울 어딘가") is None
    b = building_for(prop(), {key: _title()})
    assert b.available and b.use_approval_date == "2012-10-04" and b.use_approval_year == 2012
    assert b.elevators == 0 and b.parking == 5 and b.floors_above == 5 and len(b.notes) == 1
    neigh = building_for(prop(), {key: _title(main_purps="제2종근린생활시설")})
    assert any("근린생활시설" in n for n in neigh.notes)
    multi = building_for(prop(), {key: _title(match_status="multiple")})
    assert not multi.available and "여러 개" in multi.reason
    assert "수집 전" in building_for(prop(), {}).reason
    assert "조회하지 않았습니다" in building_for(prop(address="서울특별시 서대문구 대현동 1"), {key: _title()}).reason


def test_load_titles_prefers_latest_success(tmp_path, monkeypatch):
    import csv as _csv

    import app.building_register as br

    def write(name, rows):
        with (tmp_path / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(_title().keys()))
            w.writeheader()
            w.writerows(rows)

    write("building_titles_1.csv", [_title(bld_nm="옛 이름"), _title(address="서울특별시 서대문구 대현동 2", match_status="error")])
    write("building_titles_2.csv", [_title(match_status="error"), _title(address="서울특별시 서대문구 대현동 2", bld_nm="재시도")])
    monkeypatch.setattr(br, "_paths", lambda: [tmp_path / "building_titles_1.csv", tmp_path / "building_titles_2.csv"])
    br.load_titles.cache_clear()
    t = br.load_titles()
    br.load_titles.cache_clear()
    assert t["서대문구|대현동|0|53|33"]["bld_nm"] == "옛 이름"   # 나중 오류가 성공을 덮지 않음
    assert t["서대문구|대현동|0|2|0"]["bld_nm"] == "재시도"       # 나중 성공이 오류를 덮음


def test_collect_building_parse_and_summarize():
    from pipeline.collect_building import lot_key, parse_lot, summarize

    q = parse_lot("서울특별시 서대문구 대현동 53-33", {("11410", "대현동"): "1141011200"})
    assert q["bjdong_cd"] == "11200" and q["bun"] == "0053" and q["ji"] == "0033" and q["plat_gb_cd"] == "0"
    assert lot_key(q) == "11410-11200-0-0053-0033"
    assert parse_lot("서울특별시 서대문구 봉원동 1", {})["bjdong_cd"] == ""
    one = summarize([{"mainAtchGbCdNm": "주건축물", "useAprDay": "20120101", "rideUseElvtCnt": 1, "emgenUseElvtCnt": 0,
                      "indrAutoUtcnt": 2, "oudrAutoUtcnt": 3}, {"mainAtchGbCdNm": "부속건축물"}])
    assert one["match_status"] == "matched" and one["elevator_cnt"] == 1 and one["parking_cnt"] == 5 and one["n_records"] == 2
    two = summarize([{"mainAtchGbCdNm": "주건축물"}, {"mainAtchGbCdNm": "주건축물"}])
    assert two["match_status"] == "multiple" and "bld_nm" not in two
    assert summarize([])["match_status"] == "no_record"


def test_building_api(monkeypatch):
    from fastapi.testclient import TestClient

    import app.building_register as br
    from app.main import app

    monkeypatch.setattr(br, "load_titles", lambda: {})
    b = TestClient(app).get("/market/building/P001").json()
    assert b["property_id"] == "P001" and b["available"] is False
    assert TestClient(app).get("/market/building/NOPE").status_code == 404
