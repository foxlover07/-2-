"""확장된 확인 도구: 매물 칸(층·방 구조·엘리베이터 없이 오르는 층), 건축물대장 칸, 미리 계산한 주변 시설.

- 값을 모르면 빼지 않고 '확인 못 함'으로 센다.
- 주변 시설 거리가 비어 있고 500m 안 개수가 0 이면 '500m 안에 없음'으로 본다.
- 확인 문장은 서버가 조건에서 만든다 (Condition.describe / ExclusionPlan.confirm_text).
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.data import _parse_row
from app.exclusions import Condition, ExclusionPlan, ExclusionRunner, tool_catalog
from app.models import Property
from app.scoring_config import CONFIG

CFG = replace(CONFIG, reference_year=2026)


def prop(pid="P1", **kw) -> Property:
    base = dict(
        property_id=pid, university="이화여자대학교", property_type="원룸", address="서대문구 대현동 1",
        latitude=37.56, longitude=126.94, deposit_manwon=1000, monthly_rent_manwon=60, maintenance_manwon=10,
        area_m2=20, listing_checked_at=None, spot1_name=None, spot1_walk_min=None, spot2_name=None,
        spot2_walk_min=None, spot3_name=None, spot3_walk_min=None, spot4_name=None, spot4_walk_min=None,
        monthly_housing_cost_manwon=70, bld_status="matched",
    )
    base.update(kw)
    return Property(**base)


def runner(*conditions: dict) -> ExclusionRunner:
    return ExclusionRunner([ExclusionPlan(conditions=[Condition(**c) for c in conditions])], None, CFG, cache={})


def cond(tool, label, op, value, **kw) -> dict:
    return {"tool": tool, "label": label, "exclude_if": {"op": op, "value": value}, **kw}


# ---------------------------------------------------------------- 매물 칸

def test_floor_kind_excludes_basement_and_rooftop_only():
    r = runner(cond("listing_field", "반지하·옥탑", "in", ["반지하", "옥탑"], field="floor_kind"))
    assert r.check(prop(floor="반지하")) == ("반지하·옥탑", "층 종류: 반지하")
    assert r.check(prop(floor="옥탑방", building_floor="4"))[1] == "층 종류: 옥탑"
    assert r.check(prop(floor="3")) is None
    assert r.check(prop(floor="고층")) is None  # 지상(층 모름)
    assert r.check(prop(floor=None)) is None
    assert r.field_missing == {"반지하·옥탑": {"P1"}}


def test_floor_number_and_rooftop_number():
    r = runner(cond("listing_field", "2층 이하", "<=", 2, field="floor_number"))
    assert r.check(prop(floor="2")) is not None
    assert r.check(prop(floor="반지하")) is not None  # 반지하 = 0
    assert r.check(prop(floor="지하1")) is not None   # -1
    assert r.check(prop(floor="옥탑방", building_floor="5")) is None  # 6층으로 본다
    assert r.check(prop(floor="저층")) is None and r.field_missing  # 숫자 모름 → 빼지 않음


def test_stairs_floor_combines_floor_and_elevator():
    r = runner(cond("listing_field", "엘베 없는 4층 이상", ">=", 4, field="stairs_floor"))
    assert r.check(prop(floor="4", bld_elevators=0)) is not None
    assert r.check(prop(floor="5", bld_elevators=1)) is None   # 엘리베이터 있음 → 0
    assert r.check(prop(floor="3", bld_elevators=0)) is None
    assert r.check(prop(floor="5", bld_elevators=0, bld_status="multiple")) is None  # 대장 불확실 → 모름
    assert "엘베 없는 4층 이상" in r.field_missing


def test_room_type_not_in():
    r = runner(cond("listing_field", "분리형만", "not_in", ["분리형원룸", "투룸"], field="room_type"))
    assert r.check(prop(room_type="오픈형원룸"))[1] == "방 구조: 오픈형원룸"
    assert r.check(prop(room_type="분리형원룸")) is None


# ---------------------------------------------------------------- 건축물대장

def test_building_age_uses_reference_year_and_matched_only():
    r = runner(cond("building_field", "10년 넘은 건물", ">", 10, field="building_age_years"))
    assert r.check(prop(bld_use_approval_year=2008)) is not None   # 18년
    assert r.check(prop(bld_use_approval_year=2020)) is None       # 6년
    assert r.check(prop(bld_use_approval_year=2008, bld_status="no_record")) is None
    assert r.field_missing == {"10년 넘은 건물": {"P1"}}


def test_main_use_and_structure_groups():
    use = runner(cond("building_field", "근생 원룸", "in", ["근린생활시설"], field="main_use"))
    assert use.check(prop(bld_main_purpose="제2종근린생활시설"))[1] == "건물 용도: 근린생활시설"
    assert use.check(prop(bld_main_purpose="단독주택")) is None
    noise = runner(cond("building_field", "방음", "in", ["조적(벽돌·블록)", "목조"], field="structure", note="방음을 건물 구조로 간접 확인"))
    assert noise.check(prop(bld_structure="벽돌구조")) is not None
    assert noise.check(prop(bld_structure="철근콘크리트구조")) is None


# ---------------------------------------------------------------- 주변 시설 (미리 계산)

def test_amenity_nearest_and_missing_means_none_within_500m():
    far_cs = runner(cond("amenity", "편의점 300m", ">", 300, amenity="convenience", metric="nearest_m"))
    assert far_cs.check(prop(nearest_m_convenience=350, count_500m_convenience=0))[1] == "가장 가까운 편의점까지 350m"
    assert far_cs.check(prop(nearest_m_convenience=80, count_500m_convenience=12)) is None
    # 거리 없음 + 개수 0 → 500m 안에 없음 → 300m 초과로 판단
    assert far_cs.check(prop(nearest_m_convenience=None, count_500m_convenience=0))[1] == "500m 안에 편의점 없음"

    bar_close = runner(cond("amenity", "술집 50m", "<=", 50, amenity="bar", metric="nearest_m"))
    assert bar_close.check(prop(nearest_m_bar=28)) is not None
    assert bar_close.check(prop(nearest_m_bar=None, count_500m_bar=0)) is None  # 500m 안에 없음 → 가깝지 않다

    # 500m 를 넘는 기준(1km)은 '500m 안에 없음'만으로는 판단할 수 없다 → 확인 못 함
    gym = runner(cond("amenity", "빨래방 1km", ">", 1000, amenity="laundromat", metric="nearest_m"))
    assert gym.check(prop(nearest_m_laundromat=None, count_500m_laundromat=0)) is None
    assert gym.field_missing == {"빨래방 1km": {"P1"}}


def test_amenity_count():
    r = runner(cond("amenity", "노래방 많은 곳", ">=", 15, amenity="karaoke", metric="count_500m"))
    assert r.check(prop(count_500m_karaoke=20))[1] == "500m 안 노래방 20곳"
    assert r.check(prop(count_500m_karaoke=3)) is None
    assert r.check(prop()) is None and r.field_missing


# ---------------------------------------------------------------- 계획 검증·확인 문장

@pytest.mark.parametrize("bad", [
    cond("listing_field", "x", ">", 1, field="floor_kind"),                 # 종류 칸에 숫자 비교
    cond("listing_field", "x", "in", ["2층"], field="floor_kind"),          # 없는 값
    cond("listing_field", "x", "in", ["a"], field="floor_number"),          # 숫자 칸에 in
    cond("building_field", "x", ">", 1, field="area_m2"),                   # 다른 도구 칸
    cond("amenity", "x", ">", 1, amenity="gym", metric="nearest_m"),        # 없는 시설
    cond("amenity", "x", ">", 1, amenity="bar"),                            # metric 없음
    cond("amenity", "x", "in", ["a"], amenity="bar", metric="nearest_m"),   # 시설에 in
    cond("nearby_count", "x", "in", ["a"], keywords=["헬스장"], radius_m=300),
])
def test_invalid_conditions_are_rejected(bad):
    with pytest.raises(ValidationError):
        Condition(**bad)


def test_confirm_text_is_built_from_conditions():
    plan = ExclusionPlan(
        source_text="밤에 시끄럽고 오래된 집 싫어",
        conditions=[
            Condition(**cond("amenity", "술집", "<=", 50, amenity="bar", metric="nearest_m")),
            Condition(**cond("building_field", "방음", "in", ["조적(벽돌·블록)"], field="structure", note="방음을 건물 구조로 간접 확인")),
            Condition(**cond("building_field", "오래된 건물", ">", 15, field="building_age_years")),
        ],
        unsupported=["큰 도로 소음"],
        onsite_checks=["창문 이중창 여부", "옆방 소리"],
    )
    text = plan.confirm_text()
    assert "1. 가장 가까운 술집까지 거리 50m 이하인 매물 제외 (공공데이터 주변 시설)" in text
    assert "2. 건물 구조가 조적(벽돌·블록)인 매물 제외 (건축물대장, 방음을 건물 구조로 간접 확인)" in text
    assert "3. 건물 연식 15년 초과인 매물 제외 (건축물대장)" in text
    assert text.startswith("'밤에 시끄럽고 오래된 집 싫어'를 이렇게")
    assert "'큰 도로 소음'은 지금 데이터로 확인할 수 없어" in text
    assert "창문 이중창 여부, 옆방 소리" in text and text.endswith("(네 / 아니요)")


def test_onsite_checks_go_to_notes_and_plan_is_active():
    r = ExclusionRunner([ExclusionPlan(source_text="수압 약하면 싫어", onsite_checks=["샤워기·세면대 동시에 틀어 보기"])], None, CFG)
    assert r.active and r.check(prop()) is None
    assert any("샤워기" in n for n in r.notes())


def test_tool_catalog_lists_fields_from_code():
    cat = tool_catalog()
    listing = {f["field"] for f in cat["tools"]["listing_field"]["fields"]}
    assert {"floor_kind", "stairs_floor", "room_type", "monthly_cost_manwon"} <= listing
    floor_kind = next(f for f in cat["tools"]["listing_field"]["fields"] if f["field"] == "floor_kind")
    assert "반지하" in floor_kind["choices"]
    assert set(cat["tools"]["amenity"]["amenities"]) == {
        "convenience", "supermarket", "laundry", "laundromat", "pharmacy", "clinic", "bar", "karaoke"}


# ---------------------------------------------------------------- 통합 CSV 한 줄 읽기

def test_integrated_csv_row_parses_new_columns_and_ignores_others():
    row = {
        "property_id": "zigbang:1", "university": "이화여자대학교", "property_type": "원룸", "address": "서대문구 대현동 1",
        "latitude": "37.55", "longitude": "126.94", "deposit_manwon": "1000", "monthly_rent_manwon": "68",
        "maintenance_manwon": "12.0", "area_m2": "15.3", "listing_checked_at": "", "spot1_name": "ECC",
        "spot1_walk_min": "6.7", "spot2_name": "", "spot2_walk_min": "", "spot3_name": "", "spot3_walk_min": "",
        "spot4_name": "", "spot4_walk_min": "", "monthly_housing_cost_manwon": "80.0",
        "nearest_m_bar": "28.0", "count_500m_bar": "24", "nearest_m_karaoke": "",
        "bld_status": "matched", "bld_main_purpose": "제2종근린생활시설", "bld_use_approval_year": "2012.0",
        "bld_elevators": "0.0", "bld_structure": "벽돌구조",
        "market_level": "주변 실거래와 비슷한 편",  # 모델에 없는 칸은 무시
    }
    p = _parse_row(row)
    assert p.source_url is None and p.nearest_m_bar == 28.0 and p.count_500m_bar == 24.0
    assert p.nearest_m_karaoke is None and p.bld_use_approval_year == 2012.0 and p.bld_structure == "벽돌구조"


def test_josa_follows_final_consonant():
    from app.exclusions import josa
    assert josa("건물 구조", "이", "가") == "가" and josa("층 종류", "이", "가") == "가"
    assert josa("방 구조", "이", "가") == "가" and josa("건물 용도", "이", "가") == "가"
    assert josa("조적(벽돌·블록)", "이", "가") == "이" and josa("싫어", "을", "를") == "를"
    assert josa("큰 도로 소음", "은", "는") == "은"
