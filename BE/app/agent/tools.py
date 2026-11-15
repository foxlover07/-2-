"""챗봇 도구. LLM 이 고른 의도에 따라 서버가 실행하며, 값은 여기서 다시 검증한다.

- 조건 반영(apply_changes): LLM 이 뽑은 변경 → 생활 프로필. 환산·더하기·우선순위 규칙은 코드가 한다 (R-304·305·306).
- 추천(run_recommendation): 통합안 추천(app.scoring.recommend)을 그대로 부른다. 점수·순위를 바꾸지 않는다 (R-401).
- 기준 대비 문장(vs_target_lines): "상한보다 월 3만 원 쌈"처럼 코드가 만든다 (R-802·803).
- 제외 조건(plan_from_draft·merge_plans·remove_exclusions·preview): LLM 계획 → 검증 → 미리보기 → 적용 (5장).

HTTP 로 자기 서버를 부르지 않고 함수를 직접 부른다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import ValidationError

from app.agent import rules
from app.agent.schemas import AgentProfile, Comparison, PlanDraft, ProfileChanges, RecSummary, TopItem
from app.buildings import resolve
from app.exclusions import (
    AMENITIES, ONSITE_CHECKS, Condition, ExclusionPlan, Preview, available_amenities, josa, preview_plan,
)
from app.models import Property
from app.scoring import (
    FACTOR_LABELS, ClassSlot, RecommendRequest, UnknownBuildingError, budget_exclusion_reason,
    deposit_exclusion_reason, monthly_fixed_cost, recommend,
)
from app.scoring_config import CONFIG, ScoringConfig

TOP_SHOWN = 3
PYEONG_M2 = 3.3058  # 1평 (㎡). 환산은 서버만 한다 (R-304)

UNIVERSITY_ALIASES = {
    "이화여자대학교": ["이화여대", "이대", "이화", "이화여자대"],
    "고려대학교": ["고려대", "고대", "고려"],
    "서강대학교": ["서강대", "서강"],
    "서울과학기술대학교": ["서울과기대", "과기대", "서울과학기술대", "서울과기", "과기"],
}
LARGER_LABELS = {"like": "좋음", "neutral": "상관없음", "dislike": "싫음"}
FIELD_LABELS = {
    "university": "학교", "department": "학과", "department_buildings": "학과 건물", "priorities": "1·2순위",
    "budget_manwon": "유지비 상한", "deposit_budget_manwon": "보증금 상한", "desired_commute_min": "희망 통학 시간",
    "desired_area_m2": "희망 면적", "larger_area": "넓은 면적", "timetable": "시간표", "exclusions": "제외 조건",
}
REQUIRED = ("university", "department_buildings", "priorities", "budget_manwon", "desired_commute_min", "desired_area_m2")
# 상태를 바꾸지 않는 '연락' 칸 (되묻기 대상): 필수 칸 + 시간표 강의동
ASK_TEXT = {
    "university": "학교(이화여대·고려대·서강대·서울과기대 중에서)",
    "priorities": "가장 중요한 것 1·2순위(예산·이동 시간·면적 중에서, 예: 1순위 이동 시간, 2순위 예산)",
    "priorities.second": "2순위(예산·이동 시간·면적 중에서 1순위 말고 하나)",
    "budget_manwon": "월 유지비 상한(월세 + 관리비, 예: 70만 원)",
    "desired_commute_min": "희망 통학 시간(편도 도보, 예: 15분)",
    "desired_area_m2": "희망 면적(예: 20㎡ 또는 6평)",
    "timetable.building": "수업하는 강의동",
    "timetable.time": "수업 요일과 시작·종료 시각(예: 월 09:00~10:15)",
}


def _g(x: float | None) -> str:
    if x is None:
        return "없음"
    return f"{round(x, 2):g}"


def normalize_university(text: str | None) -> str | None:
    if not text:
        return None
    t = text.replace(" ", "")
    for name, aliases in UNIVERSITY_ALIASES.items():
        if t == name or t in aliases:
            return name
    return None


def buildings_for(university: str | None) -> list[str]:
    return list(rules.buildings_by_university().get(university or "", ()))


def resolve_buildings(names: Iterable[str], university: str) -> tuple[list[str], list[str]]:
    """사용자 건물 이름 → (표준 이름, 찾지 못한 이름). 줄임말·띄어쓰기·가운뎃점 차이를 맞춘다 (R-309)."""
    known = buildings_for(university)
    ok: list[str] = []
    unknown: list[str] = []
    for n in names:
        to = resolve(n, known)
        (ok if to in known else unknown).append(to if to in known else n)
    return list(dict.fromkeys(ok)), list(dict.fromkeys(unknown))


def ask_text(key: str, profile: AgentProfile) -> str:
    if key == "department_buildings":
        b = buildings_for(profile.university)
        return f"수업을 주로 듣는 건물({', '.join(b)} 중에서, 여러 개 가능)" if b else "수업을 주로 듣는 건물"
    if key == "timetable.building" and profile.university:
        return f"수업하는 강의동({', '.join(buildings_for(profile.university))} 중에서)"
    return ASK_TEXT.get(key, FIELD_LABELS.get(key, key))


# ---------------------------------------------------------------- 조건 반영 (서버 검증)

def _unknown_building_note(unknown: list[str], university: str) -> str:
    names = ", ".join(f"'{u}'" for u in unknown)
    return (f"{names}{josa(unknown[-1], '은', '는')} 지금 데이터에 없어요. "
            f"{university}는 {', '.join(buildings_for(university))} 중에서 고를 수 있어요.")


def apply_changes(profile: AgentProfile, ch: ProfileChanges) -> tuple[AgentProfile, list[str], list[str]]:
    """LLM 이 뽑은 변경을 검증해 반영한다. → (새 프로필, 안내 문장, 다시 물을 칸)."""
    p = profile.model_copy(deep=True)
    notes: list[str] = []
    asks: list[str] = []

    if ch.university is not None:
        uni = normalize_university(ch.university)
        if uni is None:
            notes.append(f"'{ch.university}'{josa(ch.university, '은', '는')} 지원하지 않는 학교예요. "
                         f"{', '.join(rules.universities())} 인근 매물만 추천할 수 있어요.")
        elif uni != p.university:
            if p.university is not None and (p.department_buildings or p.timetable):
                notes.append(f"학교가 바뀌어 {p.university} 기준 학과 건물·시간표는 비웠어요.")
            p.department_buildings, p.timetable = [], []
            p.university = uni

    if ch.department and ch.department.strip():
        p.department = ch.department.strip()

    for names, add in ((ch.department_buildings, False), (ch.add_department_buildings, True)):
        if not names:
            continue
        if p.university is None:
            notes.append("학교를 먼저 알려 주셔야 건물을 맞출 수 있어요.")
            continue
        ok, unknown = resolve_buildings(names, p.university)
        if unknown:
            notes.append(_unknown_building_note(unknown, p.university))
        if ok:
            p.department_buildings = list(dict.fromkeys([*p.department_buildings, *ok])) if add else ok

    # 우선순위 (R-306)
    cur = list(p.priorities)
    if ch.priorities and len(set(ch.priorities)) >= 2:
        p.priorities = list(dict.fromkeys(ch.priorities))[:2]
    elif ch.priority_first or (ch.priorities and len(set(ch.priorities)) == 1):
        first = ch.priority_first or ch.priorities[0]  # type: ignore[index]
        if cur and cur[0] == first:
            pass  # 이미 1순위 → 2순위 유지
        elif cur:
            p.priorities = [first, cur[0]]
        else:
            p.priorities = [first]
    if ch.priority_second:
        if p.priorities and p.priorities[0] != ch.priority_second:
            p.priorities = [p.priorities[0], ch.priority_second]
        elif not p.priorities:
            notes.append("1순위를 먼저 알려 주세요.")

    if ch.budget_manwon is not None:
        if ch.budget_manwon > 0:
            p.budget_manwon = ch.budget_manwon
        else:
            notes.append("유지비 상한은 0보다 커야 해요.")
    elif ch.budget_delta_manwon is not None:
        if p.budget_manwon is None:
            notes.append("지금 유지비 상한이 없어 늘리고 줄일 수 없어요. 상한 금액을 알려 주세요.")
            asks.append("budget_manwon")
        elif p.budget_manwon + ch.budget_delta_manwon <= 0:
            notes.append("바꾼 유지비 상한이 0 이하라 반영하지 않았어요.")
        else:
            p.budget_manwon = round(p.budget_manwon + ch.budget_delta_manwon, 2)

    if ch.clear_deposit_budget:
        p.deposit_budget_manwon = None
    elif ch.deposit_budget_manwon is not None:
        if ch.deposit_budget_manwon >= 0:
            p.deposit_budget_manwon = ch.deposit_budget_manwon
        else:
            notes.append("보증금 상한은 0 이상이어야 해요.")

    if ch.commute_not_walking:  # R-307
        notes.append("통학 시간은 걸어서 걸리는 시간으로 계산해요. 도보 몇 분까지 괜찮으세요?")
        asks.append("desired_commute_min")
    elif ch.desired_commute_min is not None:
        if ch.desired_commute_min > 0:
            p.desired_commute_min = ch.desired_commute_min
        else:
            notes.append("희망 통학 시간은 0분보다 길어야 해요.")
    elif ch.commute_delta_min is not None:
        if p.desired_commute_min is None:
            notes.append("지금 희망 통학 시간이 없어 늘리고 줄일 수 없어요.")
            asks.append("desired_commute_min")
        elif p.desired_commute_min + ch.commute_delta_min <= 0:
            notes.append("바꾼 희망 통학 시간이 0분 이하라 반영하지 않았어요.")
        else:
            p.desired_commute_min = round(p.desired_commute_min + ch.commute_delta_min, 2)

    if ch.area_value is not None:
        m2 = round(ch.area_value * PYEONG_M2, 2) if ch.area_unit == "평" else ch.area_value
        if m2 > 0:
            p.desired_area_m2 = m2
            if ch.area_unit == "평":
                notes.append(f"{_g(ch.area_value)}평은 {_g(m2)}㎡로 계산했어요.")
        else:
            notes.append("희망 면적은 0보다 커야 해요.")
    elif ch.area_delta_m2 is not None and p.desired_area_m2 is not None and p.desired_area_m2 + ch.area_delta_m2 > 0:
        p.desired_area_m2 = round(p.desired_area_m2 + ch.area_delta_m2, 2)

    if ch.larger_area is not None:
        p.larger_area = ch.larger_area

    if ch.timetable_add:  # R-308: 네 칸이 모두 있을 때만 넣는다
        slots: list[ClassSlot] = []
        missing: set[str] = set()
        for s in ch.timetable_add:
            if not s.building:
                missing.add("timetable.building")
            if not (s.day and s.start and s.end):
                missing.add("timetable.time")
            if missing:
                continue
            if p.university is None:
                missing.add("university")
                continue
            ok, unknown = resolve_buildings([s.building], p.university)  # type: ignore[list-item]
            if unknown:
                notes.append(_unknown_building_note(unknown, p.university))
                missing.add("timetable.building")
                continue
            try:
                slot = ClassSlot(day=s.day, start=s.start, end=s.end, building=ok[0])  # type: ignore[arg-type]
            except ValueError:
                notes.append(f"{s.day} {s.start}~{s.end} 시각이 올바르지 않아요.")
                missing.add("timetable.time")
                continue
            slots.append(slot)
        if missing:
            asks += sorted(missing)
        else:
            p.timetable = [*p.timetable, *(x for x in slots if x not in p.timetable)]

    return p, notes, list(dict.fromkeys(asks))


def missing_fields(p: AgentProfile) -> list[str]:
    out: list[str] = []
    if p.university is None:
        out.append("university")
    if not p.department_buildings and not p.timetable:
        out.append("department_buildings")
    if len(p.priorities) < 2:
        out.append("priorities.second" if p.priorities else "priorities")
    for k in ("budget_manwon", "desired_commute_min", "desired_area_m2"):
        if getattr(p, k) is None:
            out.append(k)
    return out


def questions(p: AgentProfile, asks: Sequence[str] = ()) -> list[str]:
    """다음에 물을 칸 (최대 2개, R-601)."""
    keys = list(dict.fromkeys([*asks, *missing_fields(p)]))
    return keys[:2]


def question_text(p: AgentProfile, keys: Sequence[str]) -> str:
    parts = [ask_text(k, p) for k in keys]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"{parts[0]}{josa(parts[0].split('(')[0], '을', '를')} 알려 주세요."
    first = parts[0].split("(")[0]
    return f"{parts[0]}{josa(first, '과', '와')} {parts[1]}{josa(parts[1].split('(')[0], '을', '를')} 알려 주세요."


# ---------------------------------------------------------------- 변경 문장 (바뀐 것, R-505·901)

def _show(key: str, v: Any) -> str:
    if v is None or v == []:
        return "없음"
    if key == "priorities":
        return " > ".join(FACTOR_LABELS.get(x, x) for x in v)
    if key == "larger_area":
        return LARGER_LABELS.get(v, v)
    if key in ("budget_manwon", "deposit_budget_manwon"):
        return f"{_g(v)}만 원"
    if key == "desired_commute_min":
        return f"{_g(v)}분"
    if key == "desired_area_m2":
        return f"{_g(v)}㎡"
    if key == "timetable":
        return ", ".join(f"{s['day']} {s['start']}~{s['end']} {s['building']}" for s in v)
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


def profile_diff(before: AgentProfile, after: AgentProfile, *, with_exclusions: bool = False) -> dict[str, Any]:
    b, a = before.model_dump(mode="json"), after.model_dump(mode="json")
    keys = [k for k in a if k != "exclusions" or with_exclusions]
    return {k: {"before": b[k], "after": a[k]} for k in keys if a[k] != b[k]}


def change_lines(before: AgentProfile, after: AgentProfile) -> list[str]:
    out = []
    for k, v in profile_diff(before, after).items():
        if k == "timetable" and len(v["after"]) > len(v["before"]):
            added = [s for s in v["after"] if s not in v["before"]]
            out.append(f"시간표에 {_show('timetable', added)} 추가")
        else:
            out.append(f"{FIELD_LABELS.get(k, k)}: {_show(k, v['before'])} → {_show(k, v['after'])}")
    return out


# ---------------------------------------------------------------- 추천 (통합안 엔진)

def to_request(p: AgentProfile) -> RecommendRequest:
    if missing_fields(p):
        raise ValueError(f"필수 조건이 빠졌습니다: {', '.join(missing_fields(p))}")
    return RecommendRequest(
        university=p.university,  # type: ignore[arg-type]
        department=p.department,
        department_buildings=p.department_buildings or list(dict.fromkeys(s.building for s in p.timetable)),  # 시간표만 있으면 시간표 건물
        priorities=tuple(p.priorities),  # type: ignore[arg-type]
        budget_manwon=p.budget_manwon,  # type: ignore[arg-type]
        deposit_budget_manwon=p.deposit_budget_manwon,
        desired_commute_min=p.desired_commute_min,  # type: ignore[arg-type]
        desired_area_m2=p.desired_area_m2,  # type: ignore[arg-type]
        larger_area=p.larger_area or "neutral",
        timetable=p.timetable,
        exclusions=p.exclusions,
    )


def vs_target_lines(r: Any, req: RecommendRequest) -> list[str]:
    """기준 대비 문장 (R-802·803). 숫자는 엔진 결과에서 코드가 만든다."""
    v = r.vs_target
    lines = []
    c = v.cost_manwon
    if c < 0:
        lines.append(f"유지비 월 {_g(r.monthly_cost_manwon)}만 원(상한보다 {_g(-c)}만 원 쌈)")
    elif c == 0:
        lines.append(f"유지비 월 {_g(r.monthly_cost_manwon)}만 원(상한과 같음)")
    else:
        lines.append(f"유지비 월 {_g(r.monthly_cost_manwon)}만 원(상한보다 {_g(c)}만 원 많음, 예산 초과)")
    if v.travel_min is not None:
        t = round(req.desired_commute_min + v.travel_min, 1)
        if v.travel_min < 0:
            lines.append(f"편도 도보 평균 {_g(t)}분(희망보다 {_g(-v.travel_min)}분 가까움)")
        elif v.travel_min == 0:
            lines.append(f"편도 도보 평균 {_g(t)}분(희망과 같음)")
        else:
            lines.append(f"편도 도보 평균 {_g(t)}분(희망보다 {_g(v.travel_min)}분 멂)")
    a = v.area_m2
    if a > 0:
        lines.append(f"면적 {_g(r.area_m2)}㎡(희망보다 {_g(a)}㎡ 넓음)")
    elif a == 0:
        lines.append(f"면적 {_g(r.area_m2)}㎡(희망과 같음)")
    else:
        lines.append(f"면적 {_g(r.area_m2)}㎡(희망보다 {_g(-a)}㎡ 좁음)")
    return lines


def run_recommendation(p: AgentProfile, props: Sequence[Property], travel: dict[str, dict[str, float]],
                       searcher: Any = None, cfg: ScoringConfig = CONFIG) -> RecSummary:
    """통합안 추천을 그대로 부른다 (R-401). 점수·순위는 엔진 결과 그대로."""
    req = to_request(p)
    result = recommend(props, req, travel=travel, searcher=searcher, cfg=cfg)
    by_id = {x.property_id: x for x in props}
    top: list[TopItem] = []
    for r in result.ranked[:TOP_SHOWN]:
        prop = by_id.get(r.property_id)
        top.append(TopItem(
            rank=r.rank, property_id=r.property_id, score=r.score,
            address=prop.address if prop else None, property_type=prop.property_type if prop else None,
            monthly_cost_manwon=r.monthly_cost_manwon, deposit_manwon=r.deposit_manwon, area_m2=r.area_m2,
            over_budget=r.over_budget, source_url=prop.source_url if prop else None,
            travel_avg_min=None if r.vs_target.travel_min is None else round(req.desired_commute_min + r.vs_target.travel_min, 1),
            vs_target=vs_target_lines(r, req),
        ))
    return RecSummary(
        university=req.university, priorities=list(result.priorities), weights=result.weights,
        total_ranked=len(result.ranked), excluded_count=len(result.excluded),
        exclusion_counts=result.exclusion_counts, top=top,
        not_recommended=[n.message for n in result.not_recommended],
        ranks={r.property_id: (r.rank, r.score) for r in result.ranked},
        notes=result.notes, request=req.model_dump(mode="json"),
    )


def compare(before: RecSummary, after: RecSummary, p_before: AgentProfile, p_after: AgentProfile,
            label_before: str = "이전 조건", label_after: str = "바꾼 조건") -> Comparison:
    tb = [t.property_id for t in before.top]
    ta = [t.property_id for t in after.top]
    changes = []
    for pid in dict.fromkeys(tb + ta):
        rb, ra = before.ranks.get(pid), after.ranks.get(pid)
        changes.append({"property_id": pid, "rank_before": rb[0] if rb else None, "rank_after": ra[0] if ra else None})
    return Comparison(
        label_before=label_before, label_after=label_after, total_before=before.total_ranked,
        total_after=after.total_ranked, top_before=tb, top_after=ta,
        entered_top=[x for x in ta if x not in tb], left_top=[x for x in tb if x not in ta],
        score_changes=changes, profile_diff=profile_diff(p_before, p_after, with_exclusions=True),
    )


# ---------------------------------------------------------------- 제외 조건 (5장)

# 조건 변경으로 표현되는 칸 (R-310): 제외 계획으로 만들지 않는다
PROFILE_FIELDS_IN_LISTING = {"deposit_manwon": "보증금 상한", "monthly_cost_manwon": "유지비 상한", "area_m2": "희망 면적"}
ONSITE_ALIASES = {
    "벽간 소음": "방음", "층간 소음": "방음", "소음": "방음", "햇빛": "채광", "일조": "채광", "습기": "결로·습기",
    "결로": "결로·습기", "외풍": "외풍·단열", "단열": "외풍·단열", "옵션": "옵션 상태", "바퀴벌레": "벌레",
}


def eligible_properties(p: AgentProfile, props: Iterable[Property], travel: dict[str, dict[str, float]] | None = None,
                        cfg: ScoringConfig = CONFIG) -> list[Property]:
    """미리보기 대상: 추천이 순위를 매기는 매물과 같은 기준 (R-505 '바뀐 것'의 개수가 추천 결과 개수와 맞게).

    그 대학 매물 중 예산·보증금 필터를 통과하고, 유지비·면적과 쓰는 건물까지 도보시간이 있는 것.
    """
    needed = list(dict.fromkeys([*p.department_buildings, *(s.building for s in p.timetable)]))
    out = []
    for x in props:
        if p.university and x.university != p.university:
            continue
        cost = monthly_fixed_cost(x, cfg)
        if p.budget_manwon is not None and cost is not None and budget_exclusion_reason(cost, p.budget_manwon, cfg):
            continue
        if deposit_exclusion_reason(x.deposit_manwon, p.deposit_budget_manwon, cfg):
            continue
        if cost is None or x.area_m2 is None:
            continue
        if travel is not None and needed:
            known = travel.get(x.property_id, {})
            names = list(known)
            if any(resolve(b, names) not in known for b in needed):
                continue
        out.append(x)
    return out


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


def plan_from_draft(draft: PlanDraft, source_text: str, props: Sequence[Property] | None = None
                    ) -> tuple[ExclusionPlan, list[str]]:
    """LLM 계획 → 검증한 ExclusionPlan. 검증에 실패한 조건은 추측해서 고치지 않고 unsupported 로 보낸다."""
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
            if d.amenity in AMENITIES and d.amenity not in have:  # 데이터 칸이 없는 시설 → 카카오 실시간
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


def merge_plans(existing: Sequence[ExclusionPlan], new: ExclusionPlan) -> tuple[list[ExclusionPlan], list[str]]:
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
    # 이름이 겹치면 실행기가 구분하지 못하므로 뒤 것에 번호를 붙인다
    seen: set[str] = set()
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


def all_conditions(plans: Iterable[ExclusionPlan]) -> list[Condition]:
    return [c for p in plans for c in p.conditions]


def remove_exclusions(existing: Sequence[ExclusionPlan], name: str | None) -> tuple[list[ExclusionPlan], list[str]]:
    """'술집 조건 빼 줘' → 이름이 맞는 조건을 지운다. 'all' 이면 전부 (R-509)."""
    if not existing:
        return [], []
    if not name or name.strip().lower() in ("all", "전부", "다", "모두"):
        return [], [c.label for c in all_conditions(existing)]
    key = name.replace("조건", "").replace(" ", "")

    def hit(c: Condition) -> bool:
        words = [c.label, AMENITIES.get(c.amenity or "", ""), c.field or "", *c.keywords, c.describe()]
        return any(w and (key in w.replace(" ", "") or w.replace(" ", "") in key) for w in words if len(w.replace(" ", "")) >= 1)

    removed: list[str] = []
    out: list[ExclusionPlan] = []
    for plan in existing:
        keep = [c for c in plan.conditions if not hit(c)]
        removed += [c.label for c in plan.conditions if hit(c)]
        if keep:
            out.append(plan.model_copy(update={"conditions": keep}))
    return out, removed


def preview(p: AgentProfile, plan: ExclusionPlan, props: Sequence[Property], travel: dict[str, dict[str, float]] | None = None,
            existing: Sequence[ExclusionPlan] = (), cfg: ScoringConfig = CONFIG) -> Preview:
    return preview_plan(plan, eligible_properties(p, props, travel, cfg), cfg, existing=existing)


__all__ = [
    "UnknownBuildingError", "apply_changes", "missing_fields", "questions", "question_text", "change_lines",
    "run_recommendation", "compare", "plan_from_draft", "merge_plans", "remove_exclusions", "preview",
]
