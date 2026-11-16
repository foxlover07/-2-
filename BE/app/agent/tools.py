"""에이전트 도구. LLM 이 고른 의도에 따라 서버가 실행하며, 매개변수는 여기서 다시 검증한다.

- Recommendation: 통합안 엔진(app.scoring.recommend)을 그대로 부른다. 점수·정렬을 바꾸지 않는다.
- Analysis: Pareto · Trade-off · 민감도 · 조건 완화 (app.decision), 주변 실거래 · 편의시설 (app.market, app.amenities).
  모두 참고 분석이며 추천 순위를 바꾸지 않는다.
- Property: 매물 데이터 조회
- Building: 대학별 건물 목록·도보시간 보유 수, 표기가 다른 건물 이름 맞추기 (app.buildings.resolve)
- Comparison: 두 추천 결과의 TOP 3·후보 수·점수 변화

HTTP 로 자기 서버를 부르지 않고 함수를 직접 부른다.
"""

from __future__ import annotations

import re
from typing import Any

from app.agent.schemas import (
    PlanDraft,
    UNIVERSITIES,
    AgentProfile,
    AnalysisOut,
    Comparison,
    FrequentSummary,
    ProfileChanges,
    RecSummary,
    TopItem,
)
from app import building_walks
from app import frequent_places as fp
from app.amenities import amenities_for
from app.building_register import building_for
from app.buildings import ALIASES, normalize as normalize_building, resolve
from app.data import get_property, load_properties, load_travel_times
from app.decision.engine import EngineContext, travel_min
from app.exclusions import (
    AMENITIES, ONSITE_CHECKS, Condition, ExcludeIf, ExclusionPlan, available_amenities, josa,
)
from app.decision.pareto import ParetoRequest, TradeoffRequest, run_pareto, run_tradeoff
from app.decision.relaxation import RelaxationRequest, run_relaxation
from app.decision.sensitivity import SensitivityRequest, run_sensitivity
from app.market import compare_property
from app.place_search import category_label, local_category, place_searcher
from app.scoring import FACTOR_LABELS, ClassSlot, RecommendRequest, recommend
from app.scoring_config import DEPARTMENT_BUILDINGS

if False:  # 타입 표시용 (순환 import 없이)
    from app.agent.schemas import SlotOut, TimetableRead  # noqa: F401

TOP_SHOWN = 3  # 응답에 자세히 담는 매물 수

# 사용자가 흔히 쓰는 대학 이름 → 데이터의 university 값
UNIVERSITY_ALIASES = {
    "이화여자대학교": ["이화여대", "이대", "이화"],
    "고려대학교": ["고려대", "고대"],
    "서강대학교": ["서강대", "서강"],
    "서울과학기술대학교": ["서울과기대", "과기대", "서울과학기술대"],
}


def normalize_university(text: str | None) -> str | None:
    if not text:
        return None
    t = text.replace(" ", "")
    for name, aliases in UNIVERSITY_ALIASES.items():
        if t == name or t in aliases:
            return name
    return None


# ---------------------------------------------------------------- Property / Building

def university_listing_count(university: str) -> int:
    return sum(1 for p in load_properties() if p.university == university)


def property_info(property_id: str) -> dict[str, Any] | None:
    p = get_property(property_id)
    if p is None:
        return None
    return {
        "property_id": p.property_id, "university": p.university, "property_type": p.property_type,
        "address": p.address, "deposit_manwon": p.deposit_manwon, "monthly_rent_manwon": p.monthly_rent_manwon,
        "maintenance_manwon": p.maintenance_manwon, "area_m2": p.area_m2, "source_url": p.source_url,
        "listing_checked_at": p.listing_checked_at,
    }


def building_coverage(university: str) -> dict[str, int]:
    """건물 이름 → 도보시간이 있는 매물 수 (그 대학 매물 기준)."""
    props = [p for p in load_properties() if p.university == university]
    counts: dict[str, int] = {}
    for row in load_travel_times(props).values():
        for name in row:
            counts[name] = counts.get(name, 0) + 1
    return counts


def usable_buildings(university: str, min_ratio: float = 0.25) -> list[str]:
    """추천에 고를 수 있는 건물: 매물의 1/4 이상에 도보시간이 있는 건물 + 건물 기준표(좌표가 있어 그때 계산 가능, B 방식).
    FE 생활 프로필과 같은 기준."""
    total = university_listing_count(university)
    cov = building_coverage(university)
    names = {n for n, c in cov.items() if total and c / total >= min_ratio} | set(building_walks.catalog_names(university))
    return sorted(names, key=lambda n: (-cov.get(n, 0), n))


def resolve_buildings(names: list[str], university: str) -> tuple[list[str], list[str]]:
    """사용자 건물 이름 → (표준 이름, 찾지 못한 이름). 줄임말·띄어쓰기·가운뎃점 차이를 맞춘다."""
    known = usable_buildings(university)
    ok: list[str] = []
    unknown: list[str] = []
    for n in names:
        to = resolve(n, known)
        (ok if to in known else unknown).append(to if to in known else n)
    return list(dict.fromkeys(ok)), unknown


# ---------------------------------------------------------------- 프로필 갱신 (서버 검증)

def apply_changes(profile: AgentProfile, ch: ProfileChanges) -> tuple[AgentProfile, list[str]]:
    """LLM 이 뽑은 변경을 검증해 프로필에 반영한다. 반영하지 못한 이유를 notes 로 돌려준다."""
    p = profile.model_copy(deep=True)
    notes: list[str] = []

    if ch.university is not None:
        uni = normalize_university(ch.university)
        if uni is None:
            notes.append(f"'{ch.university}'은(는) 지원하는 대학이 아닙니다 (가능: {', '.join(UNIVERSITIES)}).")
        elif uni != p.university:
            if p.university is not None and (p.department_buildings or p.timetable):
                notes.append(f"학교가 바뀌어 {p.university} 건물 기준 학과 건물·시간표는 비웠습니다.")
                p.department_buildings, p.timetable = [], []
            p.university = uni

    if ch.budget_manwon is not None:
        if ch.budget_manwon > 0:
            p.budget_manwon = ch.budget_manwon
        else:
            notes.append("유지비 상한은 0보다 커야 합니다.")
    elif ch.budget_delta_manwon is not None:
        if p.budget_manwon is None:
            notes.append("지금 유지비 상한이 없어 '얼마 올리기'를 계산할 수 없습니다. 상한 금액을 알려 주세요.")
        elif p.budget_manwon + ch.budget_delta_manwon <= 0:
            notes.append("바꾼 유지비 상한이 0 이하라 반영하지 않았습니다.")
        else:
            p.budget_manwon = p.budget_manwon + ch.budget_delta_manwon

    if ch.deposit_budget_manwon is not None:
        if ch.deposit_budget_manwon >= 0:
            p.deposit_budget_manwon = ch.deposit_budget_manwon
        else:
            notes.append("보증금 상한은 0 이상이어야 합니다.")

    if ch.department is not None and ch.department.strip():
        p.department = ch.department.strip()
    if ch.grade is not None and ch.grade >= 1:
        p.grade = ch.grade

    if ch.priorities is not None:
        pri = list(dict.fromkeys(ch.priorities))
        if len(pri) >= 2:
            p.priorities = pri[:2]
        elif len(pri) == 1:
            # 1순위만 말하면 기존 순서에서 그 요소를 맨 앞으로 (2순위는 기존 순서를 따른다)
            rest = [f for f in p.priorities if f != pri[0]]
            if rest:
                p.priorities = [pri[0], rest[0]]
            else:
                notes.append(f"1순위 {FACTOR_LABELS[pri[0]]}은(는) 알겠어요. 2순위(이동 시간·예산·면적 중)도 알려 주세요.")
                p.priorities = [pri[0]]

    for key, label in (("desired_commute_min", "희망 통학 시간"), ("desired_area_m2", "희망 면적")):
        v = getattr(ch, key)
        if v is not None:
            if v > 0:
                setattr(p, key, v)
            else:
                notes.append(f"{label} {v}은(는) 0보다 커야 해서 반영하지 않았습니다.")
    if ch.larger_area is not None:
        p.larger_area = ch.larger_area

    uni = p.university
    if ch.department_buildings is not None:
        if uni is None:
            notes.append("학교를 먼저 알려 주셔야 건물을 맞출 수 있습니다.")
        else:
            ok, unknown = resolve_buildings(ch.department_buildings, uni)
            if unknown:
                notes.append(_unknown_note(unknown, uni))
            if ok:
                p.department_buildings = ok

    if ch.timetable is not None:
        if uni is None:
            notes.append("학교를 먼저 알려 주셔야 시간표 건물을 맞출 수 있습니다.")
        else:
            slots: list[ClassSlot] = []
            bad: list[str] = []
            for s in ch.timetable:
                ok, unknown = resolve_buildings([s.building], uni)
                if unknown:
                    bad.append(s.building)
                    continue
                try:
                    slots.append(ClassSlot(day=s.day, start=s.start, end=s.end, building=ok[0]))
                except ValueError:
                    notes.append(f"시간표 {s.day} {s.start}~{s.end} 시간이 올바르지 않아 뺐습니다.")
            if bad:
                notes.append(_unknown_note(list(dict.fromkeys(bad)), uni))
            if slots or not ch.timetable:
                p.timetable = slots

    if ch.remove_exclusions:
        names = [n.strip() for n in ch.remove_exclusions if n.strip()]
        if "*" in names:
            if p.exclusions:
                notes.append("장소 조건을 모두 지웠습니다.")
            p.exclusions = []
        else:
            keep = [e for e in p.exclusions if e.source_text not in names]
            gone = [e.source_text for e in p.exclusions if e.source_text in names]
            missing = [n for n in names if n not in gone]
            if missing:
                notes.append(f"지울 장소 조건이 없습니다: {', '.join(missing)}.")
            p.exclusions = keep

    if ch.area_min_m2 is not None:
        if 0 < ch.area_min_m2 <= 200:
            plan = area_min_plan(ch.area_min_m2)
            p.exclusions = [e for e in p.exclusions if not e.source_text.startswith(AREA_MIN_PREFIX)] + [plan]
        else:
            notes.append(f"최소 면적 {ch.area_min_m2:g}㎡ 는 범위(0~200㎡)를 벗어나 반영하지 않았습니다.")

    if ch.place_prefs:
        for pref in ch.place_prefs:
            plan, note = build_exclusion_plan(pref)
            if note:
                notes.append(note)
            if plan is not None:
                p.exclusions = [e for e in p.exclusions if e.source_text != plan.source_text] + [plan]

    if ch.remove_frequent_places:
        names = [n.strip() for n in ch.remove_frequent_places if n.strip()]
        if "*" in names:
            if p.frequent_places:
                notes.append("자주 가는 곳을 모두 지웠습니다.")
            p.frequent_places = []
        else:
            def hit(f: fp.FrequentPlace, n: str) -> bool:
                return n in (f.name, f.place_name) or n in f.name or n in f.place_name
            gone = [f for f in p.frequent_places if any(hit(f, n) for n in names)]
            p.frequent_places = [f for f in p.frequent_places if f not in gone]
            if gone:
                notes.append(f"자주 가는 곳에서 뺐습니다: {', '.join(f.place_name for f in gone)}.")
            else:
                notes.append(f"지울 자주 가는 곳이 없습니다: {', '.join(names)}.")

    if ch.frequent_places:
        if p.university is None:
            notes.append("학교를 먼저 알려 주셔야 자주 가는 곳을 찾을 수 있습니다.")
        else:
            for item in ch.frequent_places:
                p, note = add_frequent_place(p, item.query or item.name, item.name, item.visits_per_week)
                notes.append(note)

    return p, notes


# ---------------------------------------------------------------- 자주 가는 곳 (app.frequent_places, 카카오)

def add_frequent_place(p: AgentProfile, query: str, name: str, visits: int | None) -> tuple[AgentProfile, str]:
    """카카오에서 찾아 바로 적용한다 (2026-11-16 결정: 확인 질문 없이, 찾은 이름·주소를 알린다). 여럿이면 되묻는다."""
    v = visits if visits and visits >= 1 else 1
    if v > 14:
        return p, f"'{name}' 주 {v}회는 범위(1~14)를 벗어나 반영하지 않았습니다."
    lk = fp.resolve_place(query, name, v, p.university or "")
    if lk.status != "ok" or lk.place is None:
        return p, fp.lookup_note(lk)
    new = lk.place
    rest = [f for f in p.frequent_places if f.place_id != new.place_id and f.name != new.name]
    if len(rest) >= fp.MAX_PLACES:
        return p, f"자주 가는 곳은 {fp.MAX_PLACES}곳까지 넣을 수 있어요. 하나를 지운 뒤 다시 알려 주세요."
    p.frequent_places = [*rest, new]
    where = f"{new.place_name}" + (f"({new.address})" if new.address else "")
    return p, (f"자주 가는 곳: '{new.name}'을(를) {where}, 주 {new.visits_per_week}회로 반영했어요 "
               "(다른 곳이면 정확한 이름을 알려 주세요).")


def request_key(p: AgentProfile) -> dict[str, Any]:
    """같은 조건인지 비교하는 열쇠 (엔진 요청 + 자주 가는 곳)."""
    return {**to_request(p).model_dump(mode="json"),
            "frequent_places": [f.model_dump(mode="json") for f in p.frequent_places]}


# ---------------------------------------------------------------- 제외 조건 (통합안 5장, 상가정보로 확인)

AVOID_RADIUS_M = 100   # '근처에 없었으면'의 기본 반경. 사용자가 거리를 말하면 그 값. ⚠️ 초기값, 확인 단계에서 보여 준다
NEAR_RADIUS_M = 300    # '가까이 있었으면'의 기본 반경
MAX_RADIUS_M = 1000


def build_exclusion_plan(pref: Any) -> tuple[ExclusionPlan | None, str | None]:
    """장소 기호 → 확인 계획 (서버가 만든다, LLM 은 장소·방향만). 확인할 수 없는 장소면 (None, 안내)."""
    place = (pref.place or "").strip()
    cat = local_category(place)
    if cat is None:
        return None, (f"'{place}'은(는) 지금 데이터(상가정보: 편의점·슈퍼마켓·세탁소·빨래방·약국·의원·주점·노래방)로 "
                      "확인할 수 없어 조건에 넣지 않았습니다.")
    shown = place if category_label(cat) == place else f"{place}({category_label(cat)})"
    radius = pref.radius_m if pref.radius_m and 0 < pref.radius_m <= MAX_RADIUS_M else None
    note = None
    if pref.radius_m and radius is None:
        note = f"'{place}' 거리 {pref.radius_m}m 는 범위(1~{MAX_RADIUS_M}m)를 벗어나 기본값을 썼습니다."
    if pref.want == "avoid":
        r = radius or AVOID_RADIUS_M
        allow = max(0, pref.max_count or 0)
        cond = Condition(tool="nearby_count", label=place, keywords=[place], radius_m=r,
                         exclude_if=ExcludeIf(op=">", value=allow))
        text = f"매물 반경 {r}m 안에 {shown}이(가) {allow + 1}곳 이상이면 제외"
    else:
        r = radius or NEAR_RADIUS_M
        cond = Condition(tool="nearest_distance", label=place, keywords=[place], radius_m=r,
                         exclude_if=ExcludeIf(op=">", value=r))
        text = f"가장 가까운 {shown}이(가) {r}m 보다 멀거나 없으면 제외"
    return ExclusionPlan(source_text=place, conditions=[cond], confirm_message=text), note


AREA_MIN_PREFIX = "면적 "


def area_min_plan(min_m2: float) -> ExclusionPlan:
    """'20㎡ 이상만' → 매물 정보(전용면적)로 확인하는 제외 조건. 희망 면적(점수 기준)은 바꾸지 않는다."""
    name = f"{AREA_MIN_PREFIX}{min_m2:g}㎡ 이상"  # 조건 이름 = 제외 수 집계·해제에 쓰는 이름
    cond = Condition(tool="listing_field", label=name, field="area_m2", exclude_if=ExcludeIf(op="<", value=min_m2))
    return ExclusionPlan(source_text=name,
                         conditions=[cond], confirm_message=f"면적이 {min_m2:g}㎡ 보다 좁으면 제외 (매물 광고의 전용면적)")


def exclusion_lines(plans: list[ExclusionPlan]) -> list[str]:
    """'바뀐 것' 줄. 장소 기호는 서버가 만든 confirm_message, 계획 단계 조건은 Condition.describe() (서버 문장)."""
    return [f"- {p.source_text}: {p.confirm_message or ' / '.join(c.describe() for c in p.conditions)}" for p in plans]


def _unknown_note(unknown: list[str], university: str) -> str:
    known = usable_buildings(university)
    return (f"{', '.join(unknown)}은(는) {university} 도보시간 데이터에 없는 건물이라 반영하지 않았습니다. "
            f"고를 수 있는 건물: {', '.join(known[:12])}{' 등' if len(known) > 12 else ''}.")


# ---------------------------------------------------------------- 필수 항목

def _preset_buildings(p: AgentProfile) -> tuple[str, ...] | None:
    """이대 학과 → 건물 예시표 (엔진과 같은 표). 없으면 None."""
    if p.university is None or p.department is None:
        return None
    return DEPARTMENT_BUILDINGS.get(p.university, {}).get(p.department)


def missing_fields(p: AgentProfile) -> list[str]:
    """통합안 엔진의 필수 입력 중 빠진 것 (FE missingForRecommend 와 같은 기준. 학년은 점수에 안 써서 묻지 않음)."""
    missing = []
    if p.university is None:
        missing.append("학교")
    if p.budget_manwon is None:
        missing.append("월 유지비(월세+관리비) 상한")
    if len(p.priorities) < 2:
        missing.append("우선순위 1·2순위 (이동 시간·예산·면적)")
    if p.desired_commute_min is None:
        missing.append("희망 통학 시간 (편도, 분)")
    if p.desired_area_m2 is None:
        missing.append("희망 면적 (㎡)")
    if p.university is not None and not p.department_buildings and _preset_buildings(p) is None:
        missing.append("수업을 주로 듣는 학과 건물")
    return missing


# ---------------------------------------------------------------- Recommendation

def to_request(p: AgentProfile) -> RecommendRequest:
    if missing_fields(p):
        raise ValueError(f"필수 조건이 빠졌습니다: {', '.join(missing_fields(p))}")
    return RecommendRequest(
        university=p.university,
        # 학과 이름은 표시용. 건물을 직접 주면 학과 예시표는 쓰지 않는다
        department=p.department or "직접 선택",
        department_buildings=p.department_buildings or None,
        # 학년은 엔진이 받기만 하고 점수에 쓰지 않는다. FE(toRecommendRequest)와 같이 없으면 1
        grade=p.grade or 1,
        priorities=(p.priorities[0], p.priorities[1]),
        budget_manwon=p.budget_manwon,
        deposit_budget_manwon=p.deposit_budget_manwon,
        desired_commute_min=p.desired_commute_min,
        desired_area_m2=p.desired_area_m2,
        larger_area=p.larger_area or "neutral",
        timetable=p.timetable,
        exclusions=p.exclusions,
    )


def run_recommendation(p: AgentProfile) -> RecSummary:
    """통합안 엔진을 그대로 부른다 (POST /recommend 와 같은 함수·데이터). 점수·순위는 엔진 결과 그대로.

    자주 가는 곳이 있으면 2단계: 1차 순위의 상위 fp.TOP_N 매물만 카카오 도보 경로로 계산하고,
    그 안에서만 다시 정렬한다 (엔진의 frequent / frequent_rerank). 자주 가는 곳이 없으면 POST /recommend 와 같다.
    """
    req = to_request(p)
    props = load_properties()
    fill = building_walks.fill_for_request(props, req, load_travel_times(props))  # 고른 건물 빈 도보시간만 계산
    travel = fill.travel
    searcher = place_searcher() if req.exclusions else None
    result = recommend(props, req, travel=travel, searcher=searcher)
    result.notes.extend(fill.notes())
    frequent_summary: FrequentSummary | None = None
    per_place: list[tuple[fp.FrequentPlace, dict[str, float | None]]] = []
    if p.frequent_places and result.ranked:
        head = [r.property_id for r in result.ranked[: fp.TOP_N]]
        head_props = [x for x in (get_property(pid) for pid in head) if x is not None]
        stats = fp.WalkStats()
        for place in p.frequent_places:
            mins, st = fp.walk_minutes(head_props, place)
            per_place.append((place, mins))
            stats.add(st)
        avg = fp.weighted_avg([(pl.visits_per_week, m) for pl, m in per_place], head)
        result = recommend(props, req, travel=travel, searcher=searcher, frequent=avg, frequent_rerank=head)
        unknown = len(head) - len(avg)
        frequent_summary = FrequentSummary(
            places=[{"name": f.name, "place_name": f.place_name, "address": f.address,
                     "visits_per_week": f.visits_per_week} for f in p.frequent_places],
            top_n=len(head), computed=stats.computed, cached=stats.cached, unknown=unknown, limited=stats.limited,
        )
        result.notes.append(
            f"자주 가는 곳은 1차 순위 상위 {len(head)}개 매물만 도보 경로를 계산해 그 안에서 다시 정렬했습니다"
            f" (그 아래 매물은 자주 가는 곳을 반영하지 않은 순서)."
        )
        if unknown:
            why = " · 오늘 계산 한도를 다 씀" if stats.limited else ""
            result.notes.append(f"상위 {len(head)}개 중 {unknown}개는 자주 가는 곳까지 도보시간을 구하지 못해 '미확인'입니다"
                                f"{why} (그 매물은 학과 건물·시간표만으로 이동 시간을 계산).")
    used = list(dict.fromkeys([*req.resolved_department_buildings(), *(s.building for s in req.timetable)]))
    top: list[TopItem] = []
    # 같은 집일 수 있는 광고(주소·보증금·월세·관리비·면적이 같음)는 가장 앞선 것 하나만 TOP 에 넣고 나머지는 개수로 붙인다.
    # 엔진 순위는 그대로 둔다 (TOP 의 rank 는 엔진 순위라 1, 2, 4 처럼 건너뛸 수 있다). FE 매물 비교 목록과 같은 기준.
    groups: dict[tuple, list[str]] = {}
    for r in result.ranked:
        k = same_house_key(get_property(r.property_id))
        if k is not None:
            groups.setdefault(k, []).append(r.property_id)
    seen: set[tuple] = set()
    for r in result.ranked:
        if len(top) >= TOP_SHOWN:
            break
        prop = get_property(r.property_id)
        k = same_house_key(prop)
        if k is not None and k in seen:
            continue
        if k is not None:
            seen.add(k)
        times = travel.get(r.property_id, {})
        top.append(TopItem(
            rank=r.rank, property_id=r.property_id, score=r.score, factor_scores=r.factor_scores,
            contributions=r.contributions, monthly_cost_manwon=r.monthly_cost_manwon, deposit_manwon=r.deposit_manwon,
            area_m2=r.area_m2, travel_min=travel_min(r), vs_target=r.vs_target.model_dump(), over_budget=r.over_budget,
            address=prop.address if prop else None, property_type=prop.property_type if prop else None,
            source_url=prop.source_url if prop else None,
            walk_min={b: times[b] for b in used if b in times},
            same_house_ids=[x for x in groups.get(k, []) if x != r.property_id] if k is not None else [],
            frequent_walk_min={pl.name: m.get(r.property_id) for pl, m in per_place},
        ))
    return RecSummary(
        university=req.university, priorities=list(result.priorities), weights=result.weights,
        total_ranked=len(result.ranked), excluded_count=len(result.excluded), top=top,
        ranks={r.property_id: (r.rank, r.score) for r in result.ranked},
        notes=result.notes, request=request_key(p), exclusion_counts=result.exclusion_counts,
        frequent=frequent_summary,
    )


def same_house_key(p: Any) -> tuple | None:
    """같은 집일 수 있는 광고 묶음 키 (FE utils/duplicates.ts 와 같은 기준). 값이 하나라도 없으면 None (묶지 않음)."""
    if p is None:
        return None
    parts = (p.address, p.deposit_manwon, p.monthly_rent_manwon, p.maintenance_manwon, p.area_m2)
    return None if any(v is None for v in parts) else parts


# ---------------------------------------------------------------- Analysis

ANALYSIS_LABELS = {
    "pareto": "Pareto 분석", "tradeoff": "비용·통학·면적 Trade-off", "sensitivity": "조건 민감도",
    "relaxation": "조건 완화", "market": "주변 전월세 실거래 비교", "amenities": "주변 생활 편의시설",
    "building": "건축물대장 정보", "excluded": "추천에서 빠진 매물과 이유",
}

# 엔진 필터 단계 (app.scoring.ExcludedStage) → 화면 이름
EXCLUDED_STAGE_LABELS = {
    "budget": "유지비 상한(+10만 원 여유) 초과", "deposit": "보증금 상한(×1.2) 초과", "exclusion": "제외 조건",
    "missing": "계산에 필요한 정보 없음", "duplicate": "매물 ID 중복",
}
EXCLUDED_EXAMPLES = 3  # 단계마다 보여 줄 예시 매물 수


def excluded_analysis(req: RecommendRequest) -> tuple[AnalysisOut, dict[str, Any]]:
    """추천에서 빠진 매물: (1) 필터 단계별 수와 엔진이 남긴 사유 예시 (2) 순위가 밀린 매물의 비추천 사유(엔진 not_recommended).
    엔진 결과를 그대로 옮기며 사유를 새로 만들지 않는다. 같은 집일 수 있는 광고는 예시에서 하나만 보인다."""
    props = load_properties()
    result = recommend(props, req, travel=load_travel_times(props),
                       searcher=place_searcher() if req.exclusions else None)
    stages: list[dict[str, Any]] = []
    for stage, label in EXCLUDED_STAGE_LABELS.items():
        rows = [e for e in result.excluded if e.stage == stage]
        if not rows:
            continue
        sub: dict[str, int] = {}
        for e in rows:
            if e.condition:
                sub[e.condition] = sub.get(e.condition, 0) + 1
        seen: set[tuple] = set()
        examples = []
        for e in rows:
            k = same_house_key(get_property(e.property_id))
            if k is not None and k in seen:
                continue
            if k is not None:
                seen.add(k)
            examples.append({"property_id": e.property_id, "address": _addr(e.property_id), "reason": e.reason})
            if len(examples) >= EXCLUDED_EXAMPLES:
                break
        stages.append({"stage": stage, "label": label, "count": len(rows), "by_condition": sub, "examples": examples})
    nr = [{"property_id": n.property_id, "address": _addr(n.property_id), "best_rank_elsewhere": n.best_rank_elsewhere,
           "message": n.message} for n in result.not_recommended]
    out = {"total": len({p.property_id for p in props if p.university == req.university}),
           "ranked": len(result.ranked), "excluded_count": len(result.excluded), "stages": stages,
           "not_recommended": nr}
    facts = {"kind": "excluded", **{k: v for k, v in out.items()},
             "notes": ["필터에서 빠진 매물은 단계별 수와 예시만 보여 줍니다 (전체 목록은 매물 비교 화면).",
                       "비추천 사유는 다른 우선순위 조합에서는 상위권인데 지금 조건에서 밀린 매물입니다."]}
    return AnalysisOut(kind="excluded", result=out), facts


def _addr(pid: str | None) -> str | None:
    p = get_property(pid) if pid else None
    return p.address if p else None


def _with_addr(d: dict | None) -> dict | None:
    return None if d is None else {**d, "address": _addr(d.get("property_id"))}


def run_analysis(kind: str, p: AgentProfile, rec: RecSummary | None,
                 rank: int | None) -> tuple[AnalysisOut, dict[str, Any]]:
    """분석 실행 → (FE 용 전체 결과, LLM 설명용 짧은 사실). 추천 순위는 바꾸지 않는다.

    market·amenities·building 은 지난 추천의 rank 번째 매물(기본 1위)을 대상으로 한다.
    """
    req = to_request(p)
    if kind in ("market", "amenities", "building"):
        top = rec.top if rec else []
        idx = (rank or 1) - 1
        if not top or not 0 <= idx < len(top):
            return AnalysisOut(kind=kind, result=None), {
                "kind": kind, "error": f"추천 결과에 {rank or 1}위 매물이 없습니다 (상위 {len(top)}개까지 볼 수 있음)."}
        pid = top[idx].property_id
        prop = get_property(pid)
        assert prop is not None
        if kind == "market":
            m = compare_property(prop)
            facts = {"kind": kind, "rank": idx + 1, "address": prop.address, "available": m.available, "reason": m.reason,
                     "listing_monthly_rent_manwon": m.listing_monthly_rent_manwon,
                     "listing_maintenance_manwon": m.listing_maintenance_manwon,
                     "level": m.level, "n": m.n, "scope": m.scope, "rent_median": m.rent_median,
                     "rent_p25": m.rent_p25, "rent_p75": m.rent_p75, "deposit_median": m.deposit_median,
                     "listing_percentile": m.listing_percentile, "period": m.period, "notes": m.notes,
                     "source": m.source}
            return AnalysisOut(kind=kind, property_id=pid, result=m.model_dump(mode="json")), facts
        if kind == "building":
            b = building_for(prop)
            facts = {"kind": kind, "rank": idx + 1, "address": prop.address,
                     **b.model_dump(exclude={"property_id", "source"}), "source": b.source}
            return AnalysisOut(kind=kind, property_id=pid, result=b.model_dump(mode="json")), facts
        a = amenities_for(prop)
        facts = {"kind": kind, "rank": idx + 1, "address": prop.address, "available": a.available, "reason": a.reason,
                 "nearest_m": {c.label: (c.nearest.distance_m if c.nearest else None) for c in a.categories},
                 "count_500m": {c.label: c.counts.get("500", 0) for c in a.categories},
                 "notes": a.notes, "source": a.source}
        return AnalysisOut(kind=kind, property_id=pid, result=a.model_dump(mode="json")), facts

    if kind == "excluded":
        return excluded_analysis(req)

    ctx = EngineContext()
    if kind == "pareto":
        r = run_pareto(ParetoRequest(request=req), ctx)
        facts = {"kind": kind, "candidates": r.candidates, "frontier_count": len(r.frontier),
                 "dominated_count": r.dominated_count,
                 "objectives": [r.objective_labels.get(o, o) for o in r.objectives],
                 "top_k_on_frontier": [{"rank": x.rank, "address": _addr(x.property_id), "pareto": x.pareto,
                                        "dominated_by": _addr(x.dominated_by)} for x in r.top_k],
                 "frontier_examples": [
                     _with_addr(x.model_dump(include={"property_id", "rank", "monthly_cost_manwon", "travel_min", "area_m2"}))
                     for x in r.frontier[:5]],
                 "notes": r.notes}
        return AnalysisOut(kind=kind, result=r.model_dump(mode="json")), facts
    if kind == "tradeoff":
        ref = rec.top[rank - 1].property_id if rank and rec and 0 < rank <= len(rec.top) else None
        t = run_tradeoff(TradeoffRequest(request=req, reference_id=ref), ctx)
        if t is None:
            return AnalysisOut(kind=kind, result=None), {"kind": kind, "error": "조건을 통과한 매물이 없어 비교할 수 없습니다."}
        facts = {"kind": kind, "reference": _with_addr(t.reference), "cheapest": _with_addr(t.cheapest),
                 "cheapest_vs_reference": t.cheapest_vs_reference.model_dump() if t.cheapest_vs_reference else None,
                 "pay_more_for_commute": [{"extra_budget_manwon": s.extra_budget_manwon,
                                           "travel_saved_min": s.travel_saved_min, "best": _with_addr(s.best)}
                                          for s in t.pay_more_for_commute],
                 "similar_commute_cheaper": {"label": t.similar_commute_cheaper.label,
                                             "saved_manwon": t.similar_commute_cheaper.saved_manwon,
                                             "best": _with_addr(t.similar_commute_cheaper.best)},
                 "notes": t.notes}
        return AnalysisOut(kind=kind, result=t.model_dump(mode="json")), facts
    if kind == "sensitivity":
        r = run_sensitivity(SensitivityRequest(request=req), ctx)
        facts = {"kind": kind, "top_k": r.top_k, "baseline_top": [_addr(x) for x in r.baseline_top],
                 "by_kind": [k.model_dump() for k in r.by_kind],
                 "stable": [{"address": _addr(x.property_id), "in_top": x.in_top, "total": x.total,
                             "baseline_rank": x.baseline_rank} for x in r.stable[:5]],
                 "notes": r.notes}
        return AnalysisOut(kind=kind, result=r.model_dump(mode="json")), facts
    if kind == "relaxation":
        r = run_relaxation(RelaxationRequest(request=req), ctx)
        facts = {"kind": kind, "baseline_candidates": r.baseline_candidates, "target_count": r.target_count,
                 "needs_relaxation": r.needs_relaxation, "failures": r.failures.model_dump(),
                 "minimal": [{"label": o.label, "candidates": o.candidates, "added": o.added} for o in r.minimal],
                 "combined": {"label": r.combined.label, "candidates": r.combined.candidates} if r.combined else None,
                 "notes": r.notes}
        return AnalysisOut(kind=kind, result=r.model_dump(mode="json")), facts
    raise ValueError(f"모르는 분석: {kind}")


# ---------------------------------------------------------------- Comparison

def profile_diff(before: AgentProfile, after: AgentProfile) -> dict[str, Any]:
    b, a = before.model_dump(mode="json"), after.model_dump(mode="json")
    return {k: {"before": b[k], "after": a[k]} for k in a if a[k] != b[k]}


def compare(before: RecSummary, after: RecSummary, p_before: AgentProfile, p_after: AgentProfile,
            label_before: str = "이전 조건", label_after: str = "바꾼 조건") -> Comparison:
    tb = [t.property_id for t in before.top]
    ta = [t.property_id for t in after.top]
    changes = []
    for pid in dict.fromkeys(tb + ta):
        rb = before.ranks.get(pid)
        ra = after.ranks.get(pid)
        changes.append({
            "property_id": pid,
            "rank_before": rb[0] if rb else None, "score_before": rb[1] if rb else None,
            "rank_after": ra[0] if ra else None, "score_after": ra[1] if ra else None,
        })
    return Comparison(
        label_before=label_before, label_after=label_after,
        total_before=before.total_ranked, total_after=after.total_ranked,
        top_before=tb, top_after=ta,
        entered_top=[x for x in ta if x not in tb], left_top=[x for x in tb if x not in ta],
        score_changes=changes, profile_diff=profile_diff(p_before, p_after),
    )


# ---------------------------------------------------------------- 시간표 사진 → 수업 칸 (서버 검증)

FUZZY_MIN_RATIO = 0.75  # OCR 오타 허용 (예: '기정운외T교양관' ↔ '정운오IT교양관' = 0.75)
FUZZY_MIN_LEN = 5       # 짧은 표기는 비슷한 이름 추정을 하지 않는다 (오매칭 방지)
SUFFIX_MIN_LEN = 3      # 뒷부분 정확 일치로 볼 최소 글자 수


def match_building_text(text: str, university: str) -> tuple[str | None, bool]:
    """강의실 표기 → (표준 건물 이름, 추정 여부). 찾지 못하면 (None, False).

    1) 표기 그대로·호수 뗀 것을 정확히(정규화·줄임말) 맞춘다 ('ECC B146', '학109', '포관B152').
    2) OCR 이 과목명을 붙이거나 글자를 틀린 경우: 호수 뗀 글자의 뒷부분을 길게→짧게 잘라 보며
       정확히 같은 이름·줄임말이 있으면 그 건물, 5글자 이상이면 비슷한 이름(유사도 0.75 이상)으로 추정한다.
       긴 쪽부터 보므로 '…정운외T교양관' 은 줄임말 '교양관'(우당교양관)보다 '정운오IT교양관'을 먼저 찾는다.
    """
    from difflib import SequenceMatcher

    raw = text.strip()
    stripped = re.sub(r"\s*[A-Za-z]?\d+[\w-]*\s*(호|호실)?$", "", raw)
    for cand in dict.fromkeys([raw, stripped]):
        ok, _unknown = resolve_buildings([cand], university) if cand else ([], [])
        if ok:
            return ok[0], False
    known = usable_buildings(university)
    targets = {normalize_building(k): k for k in known}
    for alias, to in ALIASES.items():
        if to in known:
            targets.setdefault(normalize_building(alias), to)
    letters = re.sub(r"[^가-힣A-Za-z0-9]", "", stripped).upper()
    # 기호를 뗀 표기 전체가 이름·줄임말과 같으면 길이와 상관없이 그 건물 ("'서관 315호" → '서관' → 문과대학 서관)
    if letters in targets:
        return targets[letters], False
    # 뒷부분 정확 일치 (가장 긴 것). 2글자 이하 뒷부분은 '우주과학관' → '학관' 같은 오매칭이 나서 보지 않는다
    exact = next((letters[-n:] for n in range(len(letters), SUFFIX_MIN_LEN - 1, -1) if letters[-n:] in targets), None)
    # 비슷한 이름 (가장 긴 뒷부분부터)
    fuzzy = None
    for n in range(len(letters), FUZZY_MIN_LEN - 1, -1):
        ratio, key = max(((SequenceMatcher(None, letters[-n:], k).ratio(), k) for k in targets), default=(0.0, ""))
        if ratio >= FUZZY_MIN_RATIO:
            fuzzy = key
            break
    # 정확 일치를 우선한다. 단 짧은 줄임말('교양관')만 맞고 더 긴 표기가 다른 건물과 비슷하면
    # ('기정운외T교양관' ≈ '정운오IT교양관') 긴 쪽을 추정으로 고른다
    if exact and (len(exact) >= FUZZY_MIN_LEN or fuzzy is None or targets[fuzzy] == targets[exact]):
        return targets[exact], False
    if fuzzy:
        return targets[fuzzy], True
    return None, False


def room_building_name(text: str) -> str:
    """강의실 표기에서 건물 이름 부분: 호수를 떼고 마지막 낱말 ('이산수학(영강 정보통신관 B101호' → '정보통신관')."""
    stripped = re.sub(r"\s*[A-Za-z]?\d+[\w-]*\s*(호|호실)?$", "", text.strip())
    words = [w for w in re.split(r"[\s()\[\]'\"]+", stripped) if w]
    return words[-1] if words else ""


def lookup_room_building(text: str, university: str) -> tuple[str | None, bool]:
    """목록에 없는 건물 → 카카오 장소 검색으로 찾아 기준표에 추가 (app.building_lookup).
    → (찾은 표준 이름, 실제로 카카오에서 찾아봤는지). 검색이 꺼져 있거나 한도면 찾아본 것이 아니다."""
    from app import building_lookup

    name = room_building_name(text)
    if len(name) < 2:
        return None, False
    lk = building_lookup.find_building(university, name)
    if lk.status in ("added", "known") and lk.name:
        ok, _ = resolve_buildings([lk.name], university)
        return (ok[0] if ok else None), True
    return None, lk.status == "not_found"


def check_timetable_read(read: "TimetableRead", university: str) -> list["SlotOut"]:
    """LLM 이 읽은 칸을 검증한다: 시간 형식·순서, 건물 표준 이름(도보시간 데이터에 있는 건물만).

    저장하지 않는다 — FE 확인 화면에서 사용자가 고친 뒤 시간표에 넣는다.
    """
    from app.agent.schemas import SlotOut
    from app.scoring import to_minutes

    out: list[SlotOut] = []
    for s in read.slots:
        issue = None
        try:
            ClassSlot(day=s.day, start=s.start, end=s.end, building="x")
            if to_minutes(s.end) <= to_minutes(s.start):
                issue = "종료 시각이 시작보다 빠름"
        except ValueError:
            issue = "시각 형식 확인 필요"
        building = None
        if s.building_text.strip():
            building, guessed = match_building_text(s.building_text, university)
            found, searched = None, False
            if building is None:
                # 목록에 없으면 카카오 장소 검색으로 학교 건물을 찾아 기준표에 추가한다 (같은 이름은 한 번만 검색)
                found, searched = lookup_room_building(s.building_text, university)
                building = found
            if building is None and issue is None:
                issue = (f"'{s.building_text}'은(는) 건물 목록에 없고 카카오 지도에서도 찾지 못한 건물" if searched
                         else f"'{s.building_text}'은(는) 건물 목록에 없는 건물 · 가장 가까운 건물을 골라 주세요")
            elif found and issue is None:
                issue = f"카카오 지도에서 찾아 새로 추가한 '{building}' · 위치 확인 필요"
            elif guessed and issue is None:
                issue = f"읽은 표기와 비슷한 '{building}'(으)로 추정 · 확인 필요"
        elif issue is None:
            issue = "건물 표기 없음"
        out.append(SlotOut(day=s.day, start=s.start, end=s.end, building=building,
                           building_text=s.building_text, course=s.course, issue=issue))
    return out


# ---------------------------------------------------------------- 제외 조건 계획 (agent-integrated 에서 가져옴, 2026-11-16)
# 장소 기호(술집·편의점 등 상가 8종)는 place_prefs → build_exclusion_plan 이 그대로 맡는다.
# 그 밖의 기호(매물 칸·건축물대장·현장 확인·확인 불가)는 LLM 이 도구 목록(exclusions.tool_catalog)으로 계획하고 여기서 검증한다.

PROFILE_FIELDS_IN_LISTING = {"deposit_manwon": "보증금 상한", "monthly_cost_manwon": "유지비 상한", "area_m2": "희망 면적"}
ONSITE_ALIASES = {
    "벽간 소음": "방음", "층간 소음": "방음", "소음": "방음", "햇빛": "채광", "일조": "채광", "습기": "결로·습기",
    "결로": "결로·습기", "외풍": "외풍·단열", "단열": "외풍·단열", "옵션": "옵션 상태", "바퀴벌레": "벌레",
}


def _onsite_key(text: str) -> str | None:
    if text in ONSITE_CHECKS:
        return text
    for k in ONSITE_CHECKS:
        if k in text or text in k:
            return k
    for a, k in ONSITE_ALIASES.items():
        if a in text:
            return k
    return None


def plan_from_draft(draft: PlanDraft, source_text: str, props: Any = None) -> tuple[ExclusionPlan, list[str]]:
    """LLM 계획 → 검증한 ExclusionPlan. 검증에 실패한 조건은 추측해서 고치지 않고 unsupported·현장 확인으로 보낸다."""
    from pydantic import ValidationError

    from app.scoring_config import CONFIG

    notes: list[str] = []
    have = available_amenities(props) if props is not None else dict(AMENITIES)
    conds: list[Condition] = []
    unsupported = list(dict.fromkeys(draft.unsupported))
    onsite = [k for k in (_onsite_key(x) for x in draft.onsite_checks) if k]
    used_labels: set[str] = set()
    for d in draft.conditions:
        if d.tool == "listing_field" and d.field in PROFILE_FIELDS_IN_LISTING:
            notes.append(f"'{d.label}'{josa(d.label, '은', '는')} 제외 조건이 아니라 "
                         f"{PROFILE_FIELDS_IN_LISTING[d.field]} 조건으로 바꿔 말해 주세요.")
            continue
        raw: dict[str, Any] = {"tool": d.tool, "label": d.label.strip() or "조건",
                               "exclude_if": {"op": d.op, "value": d.values if d.op in ("in", "not_in") else d.value}}
        if d.tool in ("listing_field", "building_field"):
            raw["field"] = d.field
        elif d.tool == "amenity":
            if d.amenity in AMENITIES and d.amenity not in have:  # 데이터 칸이 없는 시설 → 장소 검색
                name = AMENITIES[d.amenity]
                if d.metric == "count_500m":
                    raw.update(tool="nearby_count", keywords=[name], radius_m=CONFIG.amenity_known_radius_m)
                else:
                    raw.update(tool="nearest_distance", keywords=[name], radius_m=1000)
            else:
                raw.update(amenity=d.amenity, metric=d.metric or "nearest_m")
        else:
            raw.update(keywords=d.keywords or [d.label], radius_m=d.radius_m or (300 if d.tool == "nearby_count" else None))
        base, i = raw["label"], 2
        while raw["label"] in used_labels:
            raw["label"], i = f"{base} {i}", i + 1
        try:
            c = Condition.model_validate(raw)
        except ValidationError:
            key = _onsite_key(d.label)
            if key:
                onsite.append(key)
            else:
                unsupported.append(d.label)
            continue
        used_labels.add(c.label)
        conds.append(c)
    plan = ExclusionPlan(source_text=source_text, conditions=conds, unsupported=unsupported,
                         onsite_checks=list(dict.fromkeys(onsite)))
    return plan, notes


def _target(c: Condition) -> tuple:
    if c.tool in ("listing_field", "building_field"):
        return (c.tool, c.field)
    if c.tool == "amenity":
        return ("amenity", c.amenity, c.metric)
    return ("place", c.tool, tuple(sorted(c.keywords)))


def merge_plans(existing: list[ExclusionPlan], new: ExclusionPlan) -> tuple[list[ExclusionPlan], list[str]]:
    """같은 대상을 다시 말하면 기존 조건을 새 기준으로 바꾼다 (R-510). → (새 목록, 바뀐 기존 조건 이름)."""
    targets = {_target(c) for c in new.conditions}
    replaced: list[str] = []
    out: list[ExclusionPlan] = []
    for plan in existing:
        keep = [c for c in plan.conditions if _target(c) not in targets]
        replaced += [c.label for c in plan.conditions if _target(c) in targets]
        if keep:
            out.append(plan.model_copy(update={"conditions": keep}))
    if new.conditions:
        out.append(new.model_copy(update={"unsupported": [], "onsite_checks": []}))
    seen: set[str] = set()  # 이름이 겹치면 실행기가 구분하지 못하므로 뒤 것에 번호를 붙인다
    fixed: list[ExclusionPlan] = []
    for plan in out:
        conds = []
        for c in plan.conditions:
            label, i = c.label, 2
            while label in seen:
                label, i = f"{c.label} {i}", i + 1
            seen.add(label)
            conds.append(c if label == c.label else c.model_copy(update={"label": label}))
        fixed.append(plan.model_copy(update={"conditions": conds}))
    return fixed, replaced
