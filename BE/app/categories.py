"""추천 v3: 카테고리 9개 중 3개를 순서대로 골라 점수화 (2026-11-08 팀 결정).

- 가격(보증금 상한, 유지비 = 월세 + 관리비 상한)은 1차 필터로만 쓰고 점수에는 쓰지 않는다.
- 사용자는 카테고리를 1~3개 순서대로 고른다. 가중치는 순위별 고정값(0.55 / 0.30 / 0.15, scoring_config)이며 사용자에게 받지 않는다.
- 지금 계산할 수 있는 카테고리는 주간 총 통학 시간 · 공강 귀가 · 1교시 부담 (강의동 도보시간 데이터 기준).
  나머지는 '데이터 준비 중'이라 골라도 점수를 만들지 않고, 그 비중을 나머지 카테고리에 비율대로 나눈다.
- 카테고리 점수는 매물별 절대 점수(0~100). 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
"""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import Property
from app.scoring import (
    ClassSlot,
    Excluded,
    TravelTimes,
    UnknownBuildingError,
    _by_day,
    _fmt,
    _round_half_up,
    missing_destinations,
    monthly_fixed_cost,
    time_score,
    to_minutes,
    travel_times_from_csv,
)
from app.scoring_config import CONFIG, DEPARTMENT_BUILDINGS, ScoringConfig

CategoryKey = Literal[
    "weekly_commute", "gap_home", "frequent_places", "late_return", "first_class",
    "uphill", "amenities", "quietness", "rhythm",
]


@dataclass(frozen=True)
class CategoryInfo:
    no: int
    group: str
    label: str
    description: str
    ready: bool  # 지금 데이터로 계산할 수 있는지


CATEGORIES: dict[str, CategoryInfo] = {
    "weekly_commute": CategoryInfo(1, "동선", "주간 총 통학 시간", "요일별 첫·마지막 수업 건물 ↔ 집, 일주일 치 합산", True),
    "gap_home": CategoryInfo(2, "동선", "공강 귀가 가능 여부", "공강 > 왕복 + 쉬는 시간인 공강 비율", True),
    "frequent_places": CategoryInfo(3, "동선", "자주 가는 곳까지 거리", "알바·헬스장·본가 가는 역 등 × 주간 횟수", False),
    "late_return": CategoryInfo(4, "동선", "심야 귀가 동선", "늦게 끝나는 일정 → 집 도보 시간 (안전시설은 표시만)", False),
    "first_class": CategoryInfo(5, "동선", "1교시 부담", "9시 수업 날 집에서 나와야 하는 시각", True),
    "uphill": CategoryInfo(6, "동선", "오르막 부담", "매물 → 강의 건물 고도차", False),
    "amenities": CategoryInfo(7, "생활 환경", "생활 편의시설", "편의점·마트·코인세탁소·병원 거리", False),
    "quietness": CategoryInfo(8, "생활 환경", "조용함 vs 번화함", "반경 내 술집·유흥시설 수", False),
    "rhythm": CategoryInfo(9, "생활 환경", "생활 리듬", "아침형·야행성과 늦게 여는 시설", False),
}


@dataclass(frozen=True)
class CategoryConfig:
    """v3 카테고리 설정값 (초기값, 테스트 케이스로 조정)."""

    weekly_target_default_min: float = 150.0   # 주간 통학 허용 시간 기본값 (도보, 분)
    weekly_half_life_ratio: float = 0.5        # 반감기 = 허용 시간 × 0.5 (150분이면 75분마다 절반)
    fallback_days_per_week: int = 5            # 시간표가 없을 때 학과 건물로 추정하는 등교 일수
    gap_rest_default_min: float = 30.0         # 공강에 집에서 쉬는 최소 시간
    first_class_until: str = "09:00"           # 이 시각까지 시작하는 수업을 1교시로 본다
    first_class_buffer_default_min: float = 10.0  # 도착 여유
    first_class_target_min: float = 20.0       # (도보 + 여유) 이 시간까지 85점
    first_class_half_life_min: float = 10.0
    top_k: int = 3                             # 결과 화면에서 강조할 개수


CAT_CONFIG = CategoryConfig()


# ---------------------------------------------------------------- 입력

class Answers(BaseModel):
    """고른 카테고리의 질문 답 (고르지 않은 카테고리의 답은 무시)."""

    weekly_commute_target_min: Annotated[float, Field(gt=0)] = CAT_CONFIG.weekly_target_default_min
    gap_rest_min: Annotated[float, Field(ge=0)] = CAT_CONFIG.gap_rest_default_min
    first_class_buffer_min: Annotated[float, Field(ge=0)] = CAT_CONFIG.first_class_buffer_default_min


class RecommendV3Request(BaseModel):
    upkeep_max_manwon: Annotated[float, Field(gt=0, description="유지비(월세 + 관리비) 상한, 만원")]
    deposit_max_manwon: Annotated[float | None, Field(ge=0, description="보증금 상한, 만원. 없으면 보증금 필터 생략")] = None
    department: Annotated[str, Field(min_length=1)]
    department_buildings: Annotated[list[str] | None, Field(min_length=1)] = None
    grade: Annotated[int, Field(ge=1)]
    categories: Annotated[list[CategoryKey], Field(min_length=1, max_length=3, description="1·2·3순위 순서")]
    answers: Answers = Field(default_factory=Answers)
    timetable: list[ClassSlot] = Field(default_factory=list)

    @field_validator("categories")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("같은 카테고리를 두 번 고를 수 없습니다.")
        return v

    @model_validator(mode="after")
    def _department_buildings_known(self) -> "RecommendV3Request":
        if self.department_buildings is None and self.department not in DEPARTMENT_BUILDINGS:
            raise ValueError(
                f"학과 건물을 알 수 없는 학과입니다: {self.department!r}. department_buildings 로 직접 입력해 주세요."
            )
        return self

    def resolved_department_buildings(self) -> list[str]:
        if self.department_buildings is not None:
            return list(dict.fromkeys(self.department_buildings))
        return list(DEPARTMENT_BUILDINGS[self.department])


# ---------------------------------------------------------------- 카테고리별 계산 (순수 함수)

@dataclass
class CategoryValue:
    score: float | None   # 0~100, 계산할 수 없으면 None
    label: str | None     # 화면 표시용 값 ("주 62분", "3 / 3회", "8:44 출발")
    detail: dict[str, Any]


def _hhmm(minutes: float) -> str:
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60}:{m % 60:02d}"


def weekly_commute_minutes(
    times: Mapping[str, float],
    timetable: list[ClassSlot],
    department_buildings: list[str],
    cat: CategoryConfig = CAT_CONFIG,
) -> tuple[float, str] | None:
    """주간 총 통학 시간(분)과 계산 근거.

    시간표가 있으면 요일마다 (집 → 첫 수업 건물) + (마지막 수업 건물 → 집)을 일주일 치 더한다 (수업 사이 이동 제외).
    시간표가 없으면 학과 건물 편도 평균 × 2 × 주 등교 일수(기본 5일)로 추정한다.
    도보시간을 모르는 건물이 있으면 None.
    """
    days = _by_day(timetable)
    if days:
        total = 0.0
        for slots in days.values():
            first, last = slots[0].building, slots[-1].building
            if first not in times or last not in times:
                return None
            total += times[first] + times[last]
        return total, "timetable"
    if not department_buildings or missing_destinations(times, department_buildings):
        return None
    avg = sum(times[b] for b in department_buildings) / len(department_buildings)
    return avg * 2 * cat.fallback_days_per_week, "department_fallback"


def weekly_commute(
    times: Mapping[str, float], timetable: list[ClassSlot], department_buildings: list[str], target: float,
    cfg: ScoringConfig = CONFIG, cat: CategoryConfig = CAT_CONFIG,
) -> CategoryValue:
    r = weekly_commute_minutes(times, timetable, department_buildings, cat)
    if r is None:
        return CategoryValue(None, None, {"reason": "도보시간 정보 없음"})
    minutes, source = r
    score = time_score(minutes, target, target * cat.weekly_half_life_ratio, cfg)
    return CategoryValue(score, f"주 {_fmt(_round_half_up(minutes, 0))}분", {"weekly_min": minutes, "source": source, "target_min": target})


def gap_home(times: Mapping[str, float], timetable: list[ClassSlot], rest_min: float) -> CategoryValue:
    """공강 중 집에 다녀올 수 있는 비율. 공강 > (앞 건물 → 집) + 쉬는 시간 + (집 → 다음 건물)이면 가능.

    시간표가 없거나 공강이 없으면 계산할 수 없다 (None).
    """
    days = _by_day(timetable)
    if not days:
        return CategoryValue(None, None, {"reason": "시간표 없음"})
    possible = total = 0
    for slots in days.values():
        for prev, nxt in zip(slots, slots[1:]):
            gap = to_minutes(nxt.start) - to_minutes(prev.end)
            if gap <= 0:
                continue
            if prev.building not in times or nxt.building not in times:
                return CategoryValue(None, None, {"reason": "도보시간 정보 없음"})
            total += 1
            if gap >= times[prev.building] + rest_min + times[nxt.building]:
                possible += 1
    if total == 0:
        return CategoryValue(None, None, {"reason": "공강 없음"})
    return CategoryValue(100 * possible / total, f"{possible} / {total}회", {"possible": possible, "total": total, "rest_min": rest_min})


def first_class(
    times: Mapping[str, float], timetable: list[ClassSlot], buffer_min: float,
    cfg: ScoringConfig = CONFIG, cat: CategoryConfig = CAT_CONFIG,
) -> CategoryValue:
    """1교시 부담: 첫 수업이 first_class_until(09:00)까지 시작하는 날, (도보 + 도착 여유)의 평균을 시간 점수로.

    화면에는 가장 이른 출발 시각을 보여 준다. 1교시가 없거나 시간표가 없으면 None.
    """
    days = _by_day(timetable)
    if not days:
        return CategoryValue(None, None, {"reason": "시간표 없음"})
    limit = to_minutes(cat.first_class_until)
    leads: list[float] = []
    departures: list[float] = []
    for slots in days.values():
        first = slots[0]
        start = to_minutes(first.start)
        if start > limit:
            continue
        if first.building not in times:
            return CategoryValue(None, None, {"reason": "도보시간 정보 없음"})
        lead = times[first.building] + buffer_min
        leads.append(lead)
        departures.append(start - lead)
    if not leads:
        return CategoryValue(None, None, {"reason": "1교시 없음"})
    avg = sum(leads) / len(leads)
    score = time_score(avg, cat.first_class_target_min, cat.first_class_half_life_min, cfg)
    return CategoryValue(score, f"{_hhmm(min(departures))} 출발", {"days": len(leads), "avg_lead_min": avg, "earliest_departure": _hhmm(min(departures))})


# ---------------------------------------------------------------- 필터

def filter_reason(p: Property, upkeep_max: float, deposit_max: float | None, cfg: ScoringConfig = CONFIG) -> str | None:
    """1차 필터. 걸리면 사유, 통과하면 None. 예산은 점수에 쓰지 않는다."""
    upkeep = monthly_fixed_cost(p, cfg)
    if upkeep is None:
        return "월세 또는 관리비 정보 없음"
    if upkeep > upkeep_max:
        return f"유지비가 상한보다 월 {_fmt(_round_half_up(upkeep - upkeep_max, cfg.money_decimals))}만 원 많아서 제외"
    if deposit_max is not None:
        if p.deposit_manwon is None:
            return "보증금 정보 없음"
        if p.deposit_manwon > deposit_max:
            return f"보증금이 상한보다 {_fmt(_round_half_up(p.deposit_manwon - deposit_max, cfg.money_decimals))}만 원 많아서 제외"
    return None


# ---------------------------------------------------------------- 출력

class RankedV3(BaseModel):
    rank: int
    property_id: str
    score: float | None                       # 계산할 수 있는 카테고리가 없으면 null (가격순으로만 정렬)
    category_scores: dict[str, float | None]  # 고른 카테고리별 0~100
    category_values: dict[str, str | None]    # 화면 표시 값
    upkeep_manwon: float
    deposit_manwon: float | None
    top: bool                                 # 상위 top_k (결과 화면 강조)


class CategoryMeta(BaseModel):
    key: str
    no: int
    group: str
    label: str
    description: str
    ready: bool


class RecommendV3Result(BaseModel):
    categories: list[str]          # 고른 순서
    weights: dict[str, float]      # 실제 적용 가중치 (계산 못 한 카테고리는 0)
    top_k: int
    ranked: list[RankedV3]         # 필터 통과 매물 전체
    excluded: list[Excluded]
    notes: list[str]
    assumptions: dict[str, Any]


def category_catalog() -> list[CategoryMeta]:
    return [CategoryMeta(key=k, **asdict(v)) for k, v in CATEGORIES.items()]


def recommend_v3(
    properties: Iterable[Property],
    req: RecommendV3Request,
    *,
    travel: TravelTimes | None = None,
    cfg: ScoringConfig = CONFIG,
    cat: CategoryConfig = CAT_CONFIG,
) -> RecommendV3Result:
    properties = list(properties)
    travel = travel if travel is not None else travel_times_from_csv(properties)
    notes: list[str] = []
    excluded: list[Excluded] = []
    dept = req.resolved_department_buildings()
    picked = list(req.categories)
    a = req.answers

    needed = list(dict.fromkeys(dept + [s.building for s in req.timetable]))
    if properties:
        known = sorted({name for row in travel.values() for name in row})
        unknown = [b for b in needed if b not in known]
        if unknown:
            raise UnknownBuildingError(unknown, known)

    # 1) 필터 + 카테고리 값
    seen: set[str] = set()
    rows: list[tuple[Property, float, dict[str, CategoryValue]]] = []
    for p in properties:
        if p.property_id in seen:
            excluded.append(Excluded(property_id=p.property_id, reason="매물 ID 중복"))
            continue
        seen.add(p.property_id)
        reason = filter_reason(p, req.upkeep_max_manwon, req.deposit_max_manwon, cfg)
        if reason:
            excluded.append(Excluded(property_id=p.property_id, reason=reason))
            continue
        times = travel.get(p.property_id, {})
        missing = missing_destinations(times, needed)
        if missing:
            excluded.append(Excluded(property_id=p.property_id, reason=f"도보시간 정보 없음: {', '.join(missing)}"))
            continue
        values: dict[str, CategoryValue] = {}
        for k in picked:
            if k == "weekly_commute":
                values[k] = weekly_commute(times, req.timetable, dept, a.weekly_commute_target_min, cfg, cat)
            elif k == "gap_home":
                values[k] = gap_home(times, req.timetable, a.gap_rest_min)
            elif k == "first_class":
                values[k] = first_class(times, req.timetable, a.first_class_buffer_min, cfg, cat)
            else:
                values[k] = CategoryValue(None, None, {"reason": "데이터 준비 중"})
        rows.append((p, monthly_fixed_cost(p, cfg) or 0.0, values))

    # 2) 가중치: 순위별 고정값, 모든 후보에서 계산된 카테고리만 쓰고 나머지 비중은 비율대로 나눈다
    rank_w = [cfg.weight_rank1, cfg.weight_rank2, cfg.weight_rank3][: len(picked)]
    available = [k for k in picked if rows and all(v[k].score is not None for _, _, v in rows)]
    total_w = sum(w for k, w in zip(picked, rank_w) if k in available)
    weights = {k: (w / total_w if k in available and total_w > 0 else 0.0) for k, w in zip(picked, rank_w)}
    for k in picked:
        if rows and k not in available:
            info = CATEGORIES[k]
            if not info.ready:
                why = "데이터 준비 중이라"
            else:
                reasons = {v[k].detail.get("reason") for _, _, v in rows if v[k].score is None}
                why = f"{' / '.join(sorted(r for r in reasons if r))} 때문에"
            notes.append(f"'{info.label}'은(는) {why} 계산하지 못해 비중을 나머지 기준에 나눴습니다.")
    if not req.timetable and "weekly_commute" in available:
        notes.append(f"시간표가 없어 주간 통학 시간은 학과 건물 × 주 {cat.fallback_days_per_week}일로 추정했습니다.")
    if rows and not available:
        notes.append("고른 기준을 하나도 계산할 수 없어 유지비 낮은 순으로만 정렬했습니다.")

    # 3) 순위: 점수 높은 순 → 유지비 낮은 순 → 매물 ID
    def total(values: dict[str, CategoryValue]) -> float | None:
        if not available:
            return None
        return sum(weights[k] * values[k].score for k in available)  # type: ignore[operator]

    scored = [(p, upkeep, values, total(values)) for p, upkeep, values in rows]
    scored.sort(key=lambda r: (-(r[3] if r[3] is not None else -1), r[1], r[0].property_id))
    d = cfg.score_decimals
    ranked = [
        RankedV3(
            rank=i,
            property_id=p.property_id,
            score=None if s is None else _round_half_up(s, d),
            category_scores={k: (None if v.score is None else _round_half_up(v.score, d)) for k, v in values.items()},
            category_values={k: v.label for k, v in values.items()},
            upkeep_manwon=upkeep,
            deposit_manwon=p.deposit_manwon,
            top=i <= cat.top_k and s is not None,
        )
        for i, (p, upkeep, values, s) in enumerate(scored, start=1)
    ]
    if 0 < len(ranked) < cat.top_k:
        notes.append(f"조건에 맞는 매물이 {len(ranked)}개뿐입니다.")

    return RecommendV3Result(
        categories=picked,
        weights=weights,
        top_k=cat.top_k,
        ranked=ranked,
        excluded=excluded,
        notes=notes,
        assumptions={
            "filters": {"upkeep_max_manwon": req.upkeep_max_manwon, "deposit_max_manwon": req.deposit_max_manwon},
            "weights_by_rank": dict(zip(picked, rank_w)),
            "weights_applied": weights,
            "unavailable_categories": [k for k in picked if k not in available],
            "answers": a.model_dump(),
            "department": req.department,
            "department_buildings": dept,
            "department_buildings_source": (
                "입력" if req.department_buildings is not None else "학과 → 건물 예시표 (예시값, 팀 확인 필요)"
            ),
            "timetable_days": len(_by_day(req.timetable)),
            "mode": "도보 (Kakao 도보 경로, 강의동 4곳)",
            "category_config": asdict(cat),
        },
    )
