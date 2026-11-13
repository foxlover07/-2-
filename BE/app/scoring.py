"""추천 점수 (추천 시스템 통합안 2026-11-13).

- 필터: 대학 → 예산(여유 있음) → 챗봇 제외 조건 → 정보 없음 (2장)
- 요소: 예산 · 이동 시간 · 면적 3개. 매물마다 절대 점수 0~100, 다른 매물에 따라 바뀌지 않는다 (3장)
  세 요소는 같은 규칙(기준 50점, 좋은 쪽 단위당 +5, 나쁜 쪽 단위당 −10)과
  교환비(편도 10분 = 월 5만 원 = 2㎡)를 쓴다.
- 종합: 1·2·3순위 가중치 0.55 / 0.30 / 0.15 (4장)
- 결과: 순위, 비추천 사유, 제외 목록과 사유, 계산 가정

숫자는 모두 app/scoring_config.py 에서 읽는다. 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
(v2 의 85점·반감기 시간 함수, 교통 편의성(transit), 11/8 의 예산 상대 비교는 이 규칙으로 대체했다.)
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from itertools import permutations
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.buildings import resolve
from app.exclusions import ExclusionPlan, ExclusionRunner, PlaceSearcher
from app.models import Property
from app.scoring_config import CONFIG, DEPARTMENT_BUILDINGS, EWHA, ScoringConfig


# ---------------------------------------------------------------- 공통

def _round_half_up(value: float, digits: int) -> float:
    """JS Math.round 처럼 .5 를 올린다 (파이썬 round 는 짝수로 반올림하므로 쓰지 않는다)."""
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else _round_half_up(value, digits)


def _fmt(value: float) -> str:
    """문장용 숫자: 12.0 → '12', 12.5 → '12.5'."""
    return f"{value:g}"


ExcludedStage = Literal["duplicate", "budget", "deposit", "exclusion", "missing"]


class Excluded(BaseModel):
    """추천에서 빠진 매물과 그 사유 (필터로 빠진 매물도 사유를 남긴다, 2장)."""

    property_id: str
    reason: str
    stage: ExcludedStage            # 어느 필터에서 빠졌는지 (exclusion = 챗봇 제외 조건)
    condition: str | None = None    # 챗봇 제외 조건으로 빠졌으면 그 조건 이름 (예: "술집")


# ---------------------------------------------------------------- 3.1장 공통 규칙

def standard_score(
    value: float,
    base: float,
    unit: float,
    *,
    higher_is_better: bool,
    good_slope: float | None = None,
    cfg: ScoringConfig = CONFIG,
) -> float:
    """점수 = 50 + 5 × (기준보다 좋은 쪽 단위 수)  /  50 − 10 × (기준보다 나쁜 쪽 단위 수), 0~100 으로 자른다.

    - base: 사용자가 입력한 기준 (희망 통학 시간, 유지비 상한, 희망 면적). 기준을 딱 맞추면 50점.
    - unit: 교환비 단위 (편도 10분 = 월 5만 원 = 2㎡). 같은 단위의 차이는 어느 요소에서나 같은 점수 차이.
    - good_slope: 좋은 쪽 단위당 점수. 기본은 cfg.good_slope(+5). 면적의 '넓은 쪽'은 선택지에 따라 다르다.
    """
    if unit <= 0:
        raise ValueError(f"단위는 0보다 커야 합니다: {unit}")
    units = (value - base) / unit if higher_is_better else (base - value) / unit  # 양수 = 기준보다 좋음
    slope = cfg.good_slope if good_slope is None else good_slope
    raw = cfg.base_score + (slope * units if units >= 0 else cfg.bad_slope * units)
    return min(cfg.score_max, max(cfg.score_min, raw))


def travel_minutes_score(minutes: float, desired_min: float, cfg: ScoringConfig = CONFIG) -> float:
    """이동 시간 (편도): 희망보다 가까우면 10분당 +5, 멀면 10분당 −10."""
    if minutes < 0:
        raise ValueError(f"시간은 0 이상이어야 합니다: {minutes}")
    return standard_score(minutes, desired_min, cfg.unit_travel_min, higher_is_better=False, cfg=cfg)


def cost_score(monthly_cost: float, budget_manwon: float, cfg: ScoringConfig = CONFIG) -> float:
    """예산: 유지비 상한보다 싸면 5만 원당 +5, 상한을 넘으면(여유 구간) 5만 원당 −10."""
    return standard_score(monthly_cost, budget_manwon, cfg.unit_cost_manwon, higher_is_better=False, cfg=cfg)


LargerArea = Literal["like", "neutral", "dislike"]


def larger_area_slope(larger_area: str, cfg: ScoringConfig = CONFIG) -> float:
    return {
        "like": cfg.larger_area_like_slope,
        "neutral": cfg.larger_area_neutral_slope,
        "dislike": cfg.larger_area_dislike_slope,
    }[larger_area]


def area_score(area_m2: float, desired_m2: float, larger_area: str = "neutral", cfg: ScoringConfig = CONFIG) -> float:
    """면적: 희망보다 좁으면 2㎡당 −10. 넓은 쪽은 선택지대로 (좋음 +5 · 상관없음 0 · 싫음 −2.5 / 2㎡)."""
    return standard_score(
        area_m2, desired_m2, cfg.unit_area_m2, higher_is_better=True,
        good_slope=larger_area_slope(larger_area, cfg), cfg=cfg,
    )


# ---------------------------------------------------------------- 2장 필터

def monthly_fixed_cost(p: Property, cfg: ScoringConfig = CONFIG) -> float | None:
    """유지비 = 월세 + 관리비 (만원). 둘 중 하나라도 없으면 None ('정보 없음'으로 제외)."""
    if p.monthly_rent_manwon is None or p.maintenance_manwon is None:
        return None
    return _round_half_up(p.monthly_rent_manwon + p.maintenance_manwon, cfg.money_decimals)


def budget_exclusion_reason(monthly_cost: float, budget_manwon: float, cfg: ScoringConfig = CONFIG) -> str | None:
    """유지비 > 유지비 상한 + 여유(10만 원) → 제외 사유. 통과하면 None."""
    if monthly_cost > budget_manwon + cfg.monthly_budget_slack_manwon:
        over = _round_half_up(monthly_cost - budget_manwon, cfg.money_decimals)
        return f"유지비가 상한보다 월 {_fmt(over)}만 원 많아서 제외"
    return None


def deposit_exclusion_reason(
    deposit_manwon: float | None, deposit_budget_manwon: float | None, cfg: ScoringConfig = CONFIG
) -> str | None:
    """보증금 > 보증금 상한 × 1.2 → 제외 사유 (보증금 상한을 입력한 경우만). 보증금 정보가 없으면 확인할 수 없어 제외."""
    if deposit_budget_manwon is None:
        return None
    if deposit_manwon is None:
        return "보증금 정보 없음"
    if deposit_manwon > deposit_budget_manwon * (1 + cfg.deposit_budget_slack_ratio):
        over = _round_half_up(deposit_manwon - deposit_budget_manwon, cfg.money_decimals)
        return f"보증금이 상한보다 {_fmt(over)}만 원 많아서 제외"
    return None


def is_over_budget(monthly_cost: float, budget_manwon: float) -> bool:
    """유지비 상한 ~ 상한 + 여유 사이: 추천에는 남기고 '예산 초과' 표시."""
    return monthly_cost > budget_manwon


# ---------------------------------------------------------------- 시간표 입력

Day = Literal["월", "화", "수", "목", "금", "토", "일"]

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def to_minutes(hhmm: str) -> int:
    """'09:30' → 570."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _check_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError(f"시각은 HH:MM 형식이어야 합니다: {value!r}")
    return value


class ClassSlot(BaseModel):
    """시간표 한 칸 [요일, 시작, 종료, 강의동]."""

    day: Day
    start: str
    end: str
    building: str

    @field_validator("start", "end")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        return _check_time(v)


def _by_day(timetable: Iterable[ClassSlot]) -> dict[str, list[ClassSlot]]:
    days: dict[str, list[ClassSlot]] = {}
    for slot in timetable:
        days.setdefault(slot.day, []).append(slot)
    for slots in days.values():
        slots.sort(key=lambda s: to_minutes(s.start))
    return days


# ---------------------------------------------------------------- 3.3장 이동 시간 (부분별 편도 평균)

# 매물 ID → 목적지(강의동) 이름 → 도보시간(분)
TravelTimes = dict[str, dict[str, float]]


def travel_times_from_csv(properties: Iterable[Property]) -> TravelTimes:
    """CSV 의 spot1~4 도보시간으로 강의동 이동시간 표를 만든다."""
    table: TravelTimes = {}
    for p in properties:
        row: dict[str, float] = {}
        for i in range(1, 5):
            name = getattr(p, f"spot{i}_name")
            walk = getattr(p, f"spot{i}_walk_min")
            if name is not None and walk is not None:
                row[name] = walk
        table[p.property_id] = row
    return table


def missing_destinations(times: Mapping[str, float], names: Iterable[str]) -> list[str]:
    """이 매물에서 도보시간을 모르는 목적지 (중복 없이, 들어온 순서대로)."""
    return [n for n in dict.fromkeys(names) if n not in times]


def department_avg_walk(times: Mapping[str, float], buildings: Iterable[str]) -> float | None:
    """학과 건물 편도 평균 (여러 건물이면 평균). 건물이 없거나 도보시간을 모르면 None."""
    buildings = list(dict.fromkeys(buildings))
    if not buildings or missing_destinations(times, buildings):
        return None
    return sum(times[b] for b in buildings) / len(buildings)


def timetable_avg_walk(times: Mapping[str, float], timetable: list[ClassSlot]) -> float | None:
    """시간표 편도 평균. 요일마다 집 → 첫 수업 건물, 마지막 수업 건물 → 집 (수업 사이 이동은 제외).
    시간표가 없거나 도보시간을 모르는 건물이 있으면 None."""
    days = _by_day(timetable)
    if not days:
        return None
    legs: list[float] = []
    for slots in days.values():
        first, last = slots[0].building, slots[-1].building
        if first not in times or last not in times:
            return None
        legs += [times[first], times[last]]
    return sum(legs) / len(legs)


TRAVEL_PART_LABELS: dict[str, str] = {"department": "학과 건물", "frequent": "자주 가는 곳", "timetable": "시간표"}


class TravelPart(BaseModel):
    avg_min: float     # 편도 평균 (분)
    target_min: float  # 기준 (희망 통학 시간)
    score: float       # 0~100 (3.1장 공통 규칙)
    weight: float      # 이동 시간 안에서 실제로 쓴 비중 (입력 없는 부분을 나눈 뒤)


class TravelScore(BaseModel):
    score: float
    parts: dict[str, TravelPart]

    @property
    def avg_min(self) -> float:
        """부분별 편도 평균을 비중대로 합친 값 (화면의 '희망보다 몇 분' 표시용)."""
        return sum(p.weight * p.avg_min for p in self.parts.values())


def travel_part_weights(available: Iterable[str], cfg: ScoringConfig = CONFIG) -> dict[str, float]:
    """이동 시간 내부 비중 (학과 0.4 / 자주 가는 곳 0.4 / 시간표 0.2).
    입력·데이터가 없는 부분의 비중은 나머지 부분에 비율대로 나눈다."""
    base = {
        "department": cfg.travel_weight_department,
        "frequent": cfg.travel_weight_frequent,
        "timetable": cfg.travel_weight_timetable,
    }
    available = set(available)
    kept = {k: w for k, w in base.items() if k in available}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {k: w / total for k, w in kept.items()}


def travel_score(
    desired_commute_min: float,
    *,
    department_avg_min: float | None = None,
    timetable_avg_min: float | None = None,
    cfg: ScoringConfig = CONFIG,
) -> TravelScore | None:
    """이동 시간 점수 = Σ 부분 비중 × 부분 점수. 부분 점수는 편도 평균에 3.1장 규칙(기준 = 희망 통학 시간)을 적용한 것.

    자주 가는 곳은 데이터 준비 중이라 늘 빠지고, 그 비중은 나머지에 나눈다.
    값이 None 인 부분도 비중을 나머지에 나눈다. 모든 부분이 None 이면 None.
    """
    avgs = {"department": department_avg_min, "timetable": timetable_avg_min}
    weights = travel_part_weights([k for k, v in avgs.items() if v is not None], cfg)
    if not weights:
        return None
    parts = {
        key: TravelPart(
            avg_min=avgs[key],  # type: ignore[arg-type]
            target_min=desired_commute_min,
            score=travel_minutes_score(avgs[key], desired_commute_min, cfg),  # type: ignore[arg-type]
            weight=w,
        )
        for key, w in weights.items()
    }
    return TravelScore(score=sum(p.weight * p.score for p in parts.values()), parts=parts)


# ---------------------------------------------------------------- 1.2장 입력

Factor = Literal["travel", "cost", "area"]
FACTORS: tuple[Factor, ...] = ("travel", "cost", "area")
FACTOR_LABELS: dict[str, str] = {"travel": "이동 시간", "cost": "예산", "area": "면적"}


class RecommendRequest(BaseModel):
    """POST /recommend 입력 (통합안 1.2장). v2 필드 이름을 그대로 쓰고 면적을 더했다."""

    university: Annotated[str, Field(min_length=1, description="이 대학 매물만 추천한다 (매물 CSV 의 university 값)")] = EWHA
    department: Annotated[str, Field(min_length=1)]
    department_buildings: Annotated[
        list[str] | None,
        Field(min_length=1, description="학과 건물. 비우면 학과 → 건물 예시표로 채운다 (이대만)"),
    ] = None
    grade: Annotated[int, Field(ge=1, description="학년. 지금은 받기만 하고 계산에는 쓰지 않는다")]
    priorities: tuple[Factor, Factor] = Field(description="1순위, 2순위 (travel / cost / area). 3순위는 자동")
    budget_manwon: Annotated[float, Field(gt=0, description="유지비(월세 + 관리비) 상한, 만원")]
    deposit_budget_manwon: Annotated[float | None, Field(ge=0, description="보증금 상한 (만원). 없으면 보증금 필터 생략")] = None
    desired_commute_min: Annotated[float, Field(gt=0, description="희망 통학 시간 (편도, 도보 기준, 분)")]
    desired_area_m2: Annotated[float, Field(gt=0, description="희망 면적 (㎡)")]
    larger_area: LargerArea = Field(default="neutral", description="희망보다 넓은 면적: like 좋음 / neutral 상관없음 / dislike 싫음")
    timetable: list[ClassSlot] = Field(default_factory=list)
    exclusions: list[ExclusionPlan] = Field(
        default_factory=list, description="사용자가 승인한 챗봇 확인 계획 (5장, 챗봇 미완성). 없으면 빈 목록"
    )

    @field_validator("priorities")
    @classmethod
    def _distinct_priorities(cls, v: tuple[str, str]) -> tuple[str, str]:
        if v[0] == v[1]:
            raise ValueError("1순위와 2순위는 서로 달라야 합니다.")
        return v

    @model_validator(mode="after")
    def _department_buildings_known(self) -> "RecommendRequest":
        if self.department_buildings is None and self.department not in DEPARTMENT_BUILDINGS.get(self.university, {}):
            raise ValueError(
                f"학과 건물을 알 수 없는 학과입니다: {self.department!r}. department_buildings 로 직접 입력해 주세요."
            )
        return self

    def resolved_department_buildings(self) -> list[str]:
        if self.department_buildings is not None:
            return list(dict.fromkeys(self.department_buildings))
        return list(DEPARTMENT_BUILDINGS[self.university][self.department])


class UnknownBuildingError(ValueError):
    """이동시간 데이터에 없는 건물 이름 (API 에서는 422)."""

    def __init__(self, names: list[str], known: list[str]):
        self.names = names
        super().__init__(f"데이터에 없는 건물입니다: {', '.join(names)} (가능: {', '.join(known)})")


# ---------------------------------------------------------------- 4.1장 종합 점수

def full_priorities(priorities: tuple[str, str]) -> tuple[str, str, str]:
    """1·2순위 → 1·2·3순위 (3순위는 남은 요소)."""
    third = next(f for f in FACTORS if f not in priorities)
    return (priorities[0], priorities[1], third)


def rank_weights(priorities: tuple[str, str], cfg: ScoringConfig = CONFIG) -> dict[str, float]:
    """1·2·3순위 → 0.55 / 0.30 / 0.15."""
    p1, p2, p3 = full_priorities(priorities)
    return {p1: cfg.weight_rank1, p2: cfg.weight_rank2, p3: cfg.weight_rank3}


def applied_weights(base: Mapping[str, float], available: Iterable[str]) -> dict[str, float]:
    """계산할 수 없는 요소의 비중을 나머지 요소에 비율대로 나눈다. 모든 요소 키를 돌려준다 (못 쓰면 0).
    지금은 세 요소 모두 늘 계산되지만, 데이터가 빠질 때를 대비해 일반화해 둔다."""
    available = set(available)
    total = sum(w for f, w in base.items() if f in available)
    return {f: (base[f] / total if f in available and total > 0 else 0.0) for f in FACTORS}


@dataclass
class Candidate:
    """필터를 통과한 매물의 요소별 점수 (반올림 전)."""

    property_id: str
    factor_scores: dict[str, float | None]
    travel: TravelScore | None
    monthly_cost: float
    deposit: float | None
    area: float
    over_budget: bool


def total_score(c: Candidate, weights: Mapping[str, float]) -> float:
    return sum(weights[f] * s for f, s in c.factor_scores.items() if s is not None and weights[f] > 0)


def rank_candidates(candidates: Iterable[Candidate], weights: Mapping[str, float]) -> list[Candidate]:
    """종합 점수 높은 순 → 유지비 낮은 순 → 매물 ID 순 (4.1장)."""
    return sorted(candidates, key=lambda c: (-total_score(c, weights), c.monthly_cost, c.property_id))


# ---------------------------------------------------------------- 4.2장 출력 모델

class VsTarget(BaseModel):
    """기준 대비 값 (3.6장: 화면에는 점수 대신 이 값을 보여 주는 것을 권장)."""

    travel_min: float | None   # 편도 평균 − 희망 통학 시간 (음수 = 희망보다 가까움)
    cost_manwon: float         # 유지비 − 유지비 상한 (음수 = 예산보다 쌈)
    area_m2: float             # 면적 − 희망 면적 (양수 = 희망보다 넓음)


class RankedProperty(BaseModel):
    rank: int
    property_id: str
    score: float                            # 종합 점수 0~100
    factor_scores: dict[str, float | None]  # 요소별 절대 점수 (travel / cost / area)
    contributions: dict[str, float]         # 요소별 가중치 × 점수 (합 = score)
    travel_parts: dict[str, TravelPart]     # 이동 시간 세부 (학과 건물, 시간표)
    vs_target: VsTarget
    monthly_cost_manwon: float              # 유지비
    deposit_manwon: float | None
    area_m2: float
    over_budget: bool                       # 상한 ~ 상한 + 여유 구간 → "예산 초과" 표시


class Reason(BaseModel):
    """비추천 사유의 근거 한 요소. 숫자는 코드가 만들고 LLM 은 문장만 다듬는다."""

    factor: str
    label: str
    part: str | None = None          # 이동 시간이면 점수가 가장 낮은 세부 부분 (department / timetable)
    part_label: str | None = None
    value: float | None = None       # 이동 시간: 그 부분의 편도(분), 예산: 유지비(만원), 면적: ㎡
    top1_value: float | None = None  # 지금 1위 매물의 같은 값
    target: float | None = None      # 사용자 기준: 희망 통학 시간(분), 유지비 상한(만원), 희망 면적(㎡)
    unit: str = ""
    score: float
    top1_score: float
    weighted_gap: float              # 현재 가중치 × (1위 점수 − 이 매물 점수). 클수록 1위보다 약함


class ComboRank(BaseModel):
    priorities: tuple[str, str]
    rank: int


class NotRecommended(BaseModel):
    property_id: str
    best_rank_elsewhere: int
    top_in: list[ComboRank]   # 상위 K위 안에 드는 다른 1·2순위 조합
    weak: Reason              # 지금 1위와 비교해 가중 점수 차이가 가장 큰 요소
    strong: Reason | None     # 1위보다 나은 요소 중 차이가 가장 큰 것 (없으면 null)
    message: str


class RecommendResult(BaseModel):
    priorities: tuple[str, str, str]
    weights: dict[str, float]          # 실제로 쓴 가중치 (반올림 없음)
    ranked: list[RankedProperty]       # 필터를 통과한 매물 전체
    not_recommended: list[NotRecommended]
    excluded: list[Excluded]
    exclusion_counts: dict[str, int]   # 챗봇 제외 조건 이름 → 그 조건으로 빠진 매물 수
    assumptions: dict[str, Any]
    notes: list[str]


# ---------------------------------------------------------------- 비추천 사유

def weakest_travel_part(travel: TravelScore) -> tuple[str, TravelPart]:
    """이동 시간 세부 부분 중 점수가 가장 낮은 부분 (동점이면 학과 건물 → 자주 가는 곳 → 시간표 순)."""
    order = list(TRAVEL_PART_LABELS)
    return min(travel.parts.items(), key=lambda kv: (kv[1].score, order.index(kv[0])))


@dataclass(frozen=True)
class _Targets:
    budget: float
    area: float


def _factor_reason(
    factor: str, c: Candidate, top1: Candidate, weights: Mapping[str, float], targets: _Targets, cfg: ScoringConfig
) -> Reason:
    score, top1_score = c.factor_scores[factor], top1.factor_scores[factor]
    assert score is not None and top1_score is not None
    reason = Reason(
        factor=factor,
        label=FACTOR_LABELS[factor],
        score=_round_half_up(score, cfg.score_decimals),
        top1_score=_round_half_up(top1_score, cfg.score_decimals),
        weighted_gap=_round_half_up(weights[factor] * (top1_score - score), cfg.score_decimals),
    )
    if factor == "travel" and c.travel is not None and top1.travel is not None:
        key, part = weakest_travel_part(c.travel)
        top1_part = top1.travel.parts.get(key)
        return reason.model_copy(update={
            "part": key,
            "part_label": TRAVEL_PART_LABELS[key],
            "value": _r(part.avg_min, cfg.minutes_decimals),
            "top1_value": _r(top1_part.avg_min if top1_part else None, cfg.minutes_decimals),
            "target": part.target_min,
            "unit": "분",
        })
    if factor == "cost":
        return reason.model_copy(update={
            "value": c.monthly_cost, "top1_value": top1.monthly_cost, "target": targets.budget, "unit": "만원",
        })
    if factor == "area":
        return reason.model_copy(update={
            "value": _r(c.area, cfg.area_decimals), "top1_value": _r(top1.area, cfg.area_decimals),
            "target": targets.area, "unit": "㎡",
        })
    return reason


_STRONG_PREFIX = {
    "travel": "1위 매물보다 가깝지만 ",
    "cost": "1위 매물보다 싸지만 ",
    "area": "1위 매물보다 면적 조건이 낫지만 ",
}
_TRAVEL_WHERE = {
    "department": "학과 건물까지 편도",
    "timetable": "시간표 기준 편도 평균",
    "frequent": "자주 가는 곳까지 편도 평균",
}


def _weak_sentence(r: Reason, c: Candidate, larger_area: str, cfg: ScoringConfig) -> str:
    """약한 요소 문장. 숫자는 Reason 의 값을 그대로 쓴다."""
    m, money, area = cfg.minutes_decimals, cfg.money_decimals, cfg.area_decimals
    fallback = f"{r.label} 점수가 1위 매물보다 {_fmt(_round_half_up(r.top1_score - r.score, cfg.score_decimals))}점 낮아요."
    if r.value is None or r.target is None:
        return fallback
    v, t, top1 = r.value, r.target, r.top1_value
    if r.factor == "travel":
        where = _TRAVEL_WHERE[r.part or "department"]
        if v > t:
            return f"{where} {_fmt(v)}분이라 희망 통학 시간({_fmt(t)}분)보다 {_fmt(_round_half_up(v - t, m))}분 길어요."
        if top1 is not None and v > top1:
            return f"{where} {_fmt(v)}분으로 1위 매물({_fmt(top1)}분)보다 {_fmt(_round_half_up(v - top1, m))}분 더 걸려요."
        return f"{fallback} ({where} {_fmt(v)}분)"
    if r.factor == "cost":
        if c.over_budget:
            return f"유지비가 월 {_fmt(v)}만 원으로 상한({_fmt(t)}만 원)보다 {_fmt(_round_half_up(v - t, money))}만 원 많아요."
        if top1 is not None and v > top1:
            return f"유지비가 월 {_fmt(v)}만 원으로 1위 매물({_fmt(top1)}만 원)보다 {_fmt(_round_half_up(v - top1, money))}만 원 많아요."
        return fallback
    if r.factor == "area":
        if v < t:
            return f"면적이 {_fmt(v)}㎡로 희망 면적({_fmt(t)}㎡)보다 {_fmt(_round_half_up(t - v, area))}㎡ 좁아요."
        if larger_area == "dislike" and v > t:
            return f"면적이 {_fmt(v)}㎡로 희망 면적({_fmt(t)}㎡)보다 {_fmt(_round_half_up(v - t, area))}㎡ 넓어요."
        if larger_area == "like" and top1 is not None and v < top1:
            return f"면적이 {_fmt(v)}㎡로 1위 매물({_fmt(top1)}㎡)보다 {_fmt(_round_half_up(top1 - v, area))}㎡ 좁아요."
        return fallback
    return fallback


def not_recommended(
    candidates: list[Candidate],
    priorities: tuple[str, str],
    available: Iterable[str],
    *,
    budget: float,
    desired_area: float,
    larger_area: str = "neutral",
    cfg: ScoringConfig = CONFIG,
) -> list[NotRecommended]:
    """다른 5가지 1·2순위 조합 중 어디서든 상위 K위 안인데, 지금 조합 상위 N위 밖인 매물 (4.2장).

    - 최대 M개. 많으면 다른 조합에서의 최고 순위가 높은 순 → 동점이면 ID 순.
    - 사유: 지금 1위와 비교해 가중 점수 차이가 가장 큰 요소. 점수가 없는(null) 요소는 후보에서 뺀다.
    K = not_recommended_top_k, N = displayed_top_n, M = not_recommended_max (설정 파일).
    """
    available = list(available)
    weights = applied_weights(rank_weights(priorities, cfg), available)
    current = rank_candidates(candidates, weights)
    if not current:
        return []
    top1 = current[0]
    on_screen = {c.property_id for c in current[: cfg.displayed_top_n]} | {top1.property_id}

    hits: dict[str, list[ComboRank]] = {}
    for combo in permutations(FACTORS, 2):
        if combo == tuple(priorities):
            continue
        ranked = rank_candidates(candidates, applied_weights(rank_weights(combo, cfg), available))
        for i, c in enumerate(ranked[: cfg.not_recommended_top_k], start=1):
            if c.property_id not in on_screen:
                hits.setdefault(c.property_id, []).append(ComboRank(priorities=combo, rank=i))

    by_id = {c.property_id: c for c in candidates}
    order = sorted(hits, key=lambda pid: (min(h.rank for h in hits[pid]), pid))
    targets = _Targets(budget=budget, area=desired_area)

    result: list[NotRecommended] = []
    for pid in order:
        if len(result) >= cfg.not_recommended_max:
            break
        c = by_id[pid]
        comparable = [
            f for f in FACTORS
            if c.factor_scores.get(f) is not None and top1.factor_scores.get(f) is not None and weights[f] > 0
        ]
        if not comparable:
            continue
        gaps = {f: weights[f] * (top1.factor_scores[f] - c.factor_scores[f]) for f in comparable}  # type: ignore[operator]
        weak_f = max(comparable, key=lambda f: (gaps[f], -FACTORS.index(f)))
        strong_f = min(comparable, key=lambda f: (gaps[f], FACTORS.index(f)))
        weak = _factor_reason(weak_f, c, top1, weights, targets, cfg)
        strong = _factor_reason(strong_f, c, top1, weights, targets, cfg) if gaps[strong_f] < 0 else None
        message = (_STRONG_PREFIX[strong_f] if strong else "") + _weak_sentence(weak, c, larger_area, cfg)
        result.append(NotRecommended(
            property_id=pid,
            best_rank_elsewhere=min(h.rank for h in hits[pid]),
            top_in=sorted(hits[pid], key=lambda h: h.rank),
            weak=weak,
            strong=strong,
            message=message,
        ))
    return result


# ---------------------------------------------------------------- 추천 (LLM 없이 입력 → 결과)

def recommend(
    properties: Iterable[Property],
    req: RecommendRequest,
    *,
    travel: TravelTimes | None = None,
    searcher: PlaceSearcher | None = None,
    exclusion_cache: dict | None = None,
    cfg: ScoringConfig = CONFIG,
) -> RecommendResult:
    """입력 → 필터 → 요소별 절대 점수 → 1·2·3순위 가중합 → 순위 + 비추천 사유 + assumptions.

    searcher: 챗봇 장소 조건(nearby_count, nearest_distance)을 확인할 장소 검색 (없으면 장소 조건은 확인하지 않고 안내).
    """
    # 2장 필터 1: 요청한 대학의 매물만 (다른 대학 매물은 목록에 넣지 않는다)
    properties = [p for p in properties if p.university == req.university]
    travel = travel if travel is not None else travel_times_from_csv(properties)
    travel = {p.property_id: travel.get(p.property_id, {}) for p in properties}
    notes: list[str] = []
    excluded: list[Excluded] = []
    runner = ExclusionRunner(req.exclusions, searcher, cfg, exclusion_cache) if req.exclusions else None

    # 건물 이름을 표준 이름으로 (줄임말·띄어쓰기·가운뎃점 차이, app.buildings)
    known = sorted({name for row in travel.values() for name in row})
    renamed: list[tuple[str, str]] = []

    def std(name: str) -> str:
        to = resolve(name, known) if known else name
        if to != name and (name, to) not in renamed:
            renamed.append((name, to))
        return to

    dept = list(dict.fromkeys(std(b) for b in req.resolved_department_buildings()))
    timetable = [s.model_copy(update={"building": std(s.building)}) for s in req.timetable]
    for frm, to in renamed:
        notes.append(f"건물 '{frm}'을(를) '{to}'(으)로 읽었습니다.")
    needed = list(dict.fromkeys(dept + [s.building for s in timetable]))
    if properties:
        unknown = [b for b in needed if b not in known]
        if unknown:
            raise UnknownBuildingError(unknown, known)

    # 이동 시간 부분: 학과 건물은 항상, 시간표는 입력했을 때, 자주 가는 곳은 데이터 준비 중
    part_weights = travel_part_weights(["department"] + (["timetable"] if timetable else []), cfg)
    notes.append("자주 가는 곳은 데이터 준비 중이라 이동 시간 안의 비중을 다른 부분에 비율대로 나눴습니다.")
    if not timetable:
        notes.append("시간표를 입력하지 않아 이동 시간은 학과 건물만으로 계산했습니다.")

    # 2장 필터 2~5 + 3장 요소별 점수
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for p in properties:
        pid = p.property_id
        if pid in seen:
            excluded.append(Excluded(property_id=pid, reason="매물 ID 중복", stage="duplicate"))
            continue
        seen.add(pid)

        cost = monthly_fixed_cost(p, cfg)
        if cost is not None and (reason := budget_exclusion_reason(cost, req.budget_manwon, cfg)):
            excluded.append(Excluded(property_id=pid, reason=reason, stage="budget"))
            continue
        if reason := deposit_exclusion_reason(p.deposit_manwon, req.deposit_budget_manwon, cfg):
            excluded.append(Excluded(property_id=pid, reason=reason, stage="deposit"))
            continue
        if runner is not None and (hit := runner.check(p)):
            excluded.append(Excluded(property_id=pid, reason=hit[1], stage="exclusion", condition=hit[0]))
            continue

        times = travel[pid]
        missing: list[str] = []
        if cost is None:
            missing.append("월세 또는 관리비")
        if p.area_m2 is None:
            missing.append("면적")
        if missing:
            excluded.append(Excluded(property_id=pid, reason=f"{' · '.join(missing)} 정보 없음", stage="missing"))
            continue
        if walk_missing := missing_destinations(times, needed):
            excluded.append(Excluded(property_id=pid, reason=f"도보시간 정보 없음: {', '.join(walk_missing)}", stage="missing"))
            continue

        assert cost is not None and p.area_m2 is not None
        ts = travel_score(
            req.desired_commute_min,
            department_avg_min=department_avg_walk(times, dept),
            timetable_avg_min=timetable_avg_walk(times, timetable),
            cfg=cfg,
        )
        candidates.append(Candidate(
            property_id=pid,
            factor_scores={
                "travel": ts.score if ts else None,
                "cost": cost_score(cost, req.budget_manwon, cfg),
                "area": area_score(p.area_m2, req.desired_area_m2, req.larger_area, cfg),
            },
            travel=ts,
            monthly_cost=cost,
            deposit=p.deposit_manwon,
            area=p.area_m2,
            over_budget=is_over_budget(cost, req.budget_manwon),
        ))

    if runner is not None:
        notes.extend(runner.notes())
    exclusion_counts: dict[str, int] = {}
    for e in excluded:
        if e.stage == "exclusion" and e.condition:
            exclusion_counts[e.condition] = exclusion_counts.get(e.condition, 0) + 1
    if exclusion_counts:
        total = sum(exclusion_counts.values())
        notes.append(f"챗봇 조건으로 매물 {total}개를 뺐습니다 ({', '.join(f'{k} {n}개' for k, n in exclusion_counts.items())}).")
        if not candidates:
            notes.append("조건 때문에 남은 매물이 없습니다. 조건을 풀면 다시 볼 수 있어요.")

    # 4.1장 가중치: 1·2·3순위 → 0.55/0.30/0.15, 계산 못 한 요소가 있으면 그 비중은 나머지에 나눈다
    order = full_priorities(req.priorities)
    base = rank_weights(req.priorities, cfg)
    available = [f for f in FACTORS if candidates and all(c.factor_scores[f] is not None for c in candidates)]
    weights = applied_weights(base, available)
    for f in FACTORS:
        if candidates and f not in available:
            notes.append(f"{FACTOR_LABELS[f]}은(는) 계산할 수 없어 비중을 나머지 요소에 비율대로 나눴습니다.")

    # 순위 (응답에 넣을 때만 반올림)
    d = cfg.score_decimals
    ranked = [
        RankedProperty(
            rank=i,
            property_id=c.property_id,
            score=_round_half_up(total_score(c, weights), d),
            factor_scores={f: _r(s, d) for f, s in c.factor_scores.items()},
            contributions={
                f: _round_half_up(weights[f] * s, d)
                for f, s in c.factor_scores.items()
                if s is not None and weights[f] > 0
            },
            travel_parts={
                k: part.model_copy(update={
                    "avg_min": _round_half_up(part.avg_min, cfg.minutes_decimals),
                    "score": _round_half_up(part.score, d),
                })
                for k, part in (c.travel.parts.items() if c.travel else [])
            },
            vs_target=VsTarget(
                travel_min=None if c.travel is None else _round_half_up(c.travel.avg_min - req.desired_commute_min, cfg.minutes_decimals),
                cost_manwon=_round_half_up(c.monthly_cost - req.budget_manwon, cfg.money_decimals),
                area_m2=_round_half_up(c.area - req.desired_area_m2, cfg.area_decimals),
            ),
            monthly_cost_manwon=c.monthly_cost,
            deposit_manwon=c.deposit,
            area_m2=c.area,
            over_budget=c.over_budget,
        )
        for i, c in enumerate(rank_candidates(candidates, weights), start=1)
    ]
    if 0 < len(ranked) < cfg.displayed_top_n:
        notes.append(f"조건에 맞는 매물이 {len(ranked)}개뿐입니다.")

    return RecommendResult(
        priorities=order,
        weights=weights,
        ranked=ranked,
        not_recommended=not_recommended(
            candidates, req.priorities, available,
            budget=req.budget_manwon, desired_area=req.desired_area_m2, larger_area=req.larger_area, cfg=cfg,
        ),
        excluded=excluded,
        exclusion_counts=exclusion_counts,
        assumptions={
            "university": req.university,
            "priorities": list(order),
            "weights_by_rank": base,
            "weights_applied": weights,
            "travel_part_weights_applied": part_weights,
            "unavailable_travel_parts": [k for k in TRAVEL_PART_LABELS if k not in part_weights],
            "score_rule": {
                "base_score": cfg.base_score,
                "good_slope_per_unit": cfg.good_slope,
                "bad_slope_per_unit": -cfg.bad_slope,
                "units": {"travel_min": cfg.unit_travel_min, "cost_manwon": cfg.unit_cost_manwon, "area_m2": cfg.unit_area_m2},
            },
            "desired_commute_min": req.desired_commute_min,
            "desired_area_m2": req.desired_area_m2,
            "larger_area": req.larger_area,
            "larger_area_slope_per_unit": larger_area_slope(req.larger_area, cfg),
            "budget_manwon": req.budget_manwon,
            "monthly_limit_manwon": req.budget_manwon + cfg.monthly_budget_slack_manwon,
            "deposit_budget_manwon": req.deposit_budget_manwon,
            "deposit_limit_manwon": (
                None if req.deposit_budget_manwon is None
                else _round_half_up(req.deposit_budget_manwon * (1 + cfg.deposit_budget_slack_ratio), cfg.money_decimals)
            ),
            "department": req.department,
            "department_buildings": dept,
            "department_buildings_source": (
                "입력" if req.department_buildings is not None else "학과 → 건물 예시표 (예시값, 팀 확인 필요)"
            ),
            "grade": req.grade,
            "grade_note": "학년은 받기만 하고 계산에는 쓰지 않습니다.",
            "timetable_days": len(_by_day(timetable)),
            "renamed_buildings": [{"from": f, "to": t} for f, t in renamed],
            "mode": "도보",
            "exclusions": runner.assumptions() if runner is not None else {"plans": [], "note": "챗봇 제외 조건 없음"},
            "config": asdict(cfg),
        },
        notes=notes,
    )
