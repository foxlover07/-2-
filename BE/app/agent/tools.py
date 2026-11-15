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
    UNIVERSITIES,
    AgentProfile,
    AnalysisOut,
    Comparison,
    ProfileChanges,
    RecSummary,
    TopItem,
)
from app.amenities import amenities_for
from app.building_register import building_for
from app.buildings import resolve
from app.data import get_property, load_properties, load_travel_times
from app.decision.engine import EngineContext, travel_min
from app.exclusions import Condition, ExcludeIf, ExclusionPlan
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
    """추천에 고를 수 있는 건물 (매물의 1/4 이상에 도보시간이 있는 건물, FE 와 같은 기준)."""
    total = university_listing_count(university)
    cov = building_coverage(university)
    return sorted((n for n, c in cov.items() if total and c / total >= min_ratio), key=lambda n: (-cov[n], n))


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

    if ch.place_prefs:
        for pref in ch.place_prefs:
            plan, note = build_exclusion_plan(pref)
            if note:
                notes.append(note)
            if plan is not None:
                p.exclusions = [e for e in p.exclusions if e.source_text != plan.source_text] + [plan]

    return p, notes


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


def exclusion_lines(plans: list[ExclusionPlan]) -> list[str]:
    return [f"- {p.source_text}: {p.confirm_message}" for p in plans]


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
    """통합안 엔진을 그대로 부른다 (POST /recommend 와 같은 함수·데이터). 점수·순위는 엔진 결과 그대로."""
    req = to_request(p)
    props = load_properties()
    travel = load_travel_times(props)
    result = recommend(props, req, travel=travel, searcher=place_searcher() if req.exclusions else None)
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
        ))
    return RecSummary(
        university=req.university, priorities=list(result.priorities), weights=result.weights,
        total_ranked=len(result.ranked), excluded_count=len(result.excluded), top=top,
        ranks={r.property_id: (r.rank, r.score) for r in result.ranked},
        notes=result.notes, request=req.model_dump(mode="json"), exclusion_counts=result.exclusion_counts,
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
    "building": "건축물대장 정보",
}


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
            # 'ECC B146', '학관 301호', '포관B152' → 강의실 번호를 떼고도 맞춰 본다
            stripped = re.sub(r"\s*[A-Za-z]?\d+[\w-]*(호|호실)?$", "", s.building_text.strip())
            building = None
            for cand in dict.fromkeys([s.building_text, stripped]):
                ok, _unknown = resolve_buildings([cand], university) if cand else ([], [])
                if ok:
                    building = ok[0]
                    break
            if building is None and issue is None:
                issue = f"'{s.building_text}'은(는) 도보시간 데이터에 없는 건물"
        elif issue is None:
            issue = "건물 표기 없음"
        out.append(SlotOut(day=s.day, start=s.start, end=s.end, building=building,
                           building_text=s.building_text, course=s.course, issue=issue))
    return out
