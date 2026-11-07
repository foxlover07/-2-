"""추천 점수 v2 (설계안 recommend-score-design.md).

요소는 이동시간 · 교통 편의성 · 주거비 3개이고, 각 요소는 매물별 절대 점수(0~100)다.
다른 매물에 따라 점수가 바뀌지 않는다 (min-max 정규화를 쓰지 않는다).
숫자는 모두 app/scoring_config.py 에서 읽는다. 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from itertools import permutations
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import Property
from app.scoring_config import CONFIG, DEPARTMENT_BUILDINGS, ScoringConfig


# ---------------------------------------------------------------- 공통

def _round_half_up(value: float, digits: int) -> float:
    """JS Math.round 처럼 .5 를 올린다 (파이썬 round 는 짝수로 반올림하므로 쓰지 않는다)."""
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def _fmt(value: float) -> str:
    """문장용 숫자: 12.0 → '12', 12.5 → '12.5'."""
    return f"{value:g}"


class Excluded(BaseModel):
    """추천에서 빠진 매물과 그 사유 (필터로 빠진 매물도 사유를 남긴다, 설계안 2장)."""

    property_id: str
    reason: str


# ---------------------------------------------------------------- 3장 시간 점수 함수

def time_score(t: float, target: float, half_life: float, cfg: ScoringConfig = CONFIG) -> float:
    """설계안의 score(t; T, h). 편도 t분 → 0~100 점.

    t ≤ T : score_max − (score_max − score_at_target) × t / T   (기본 100 − 15·t/T, 완만한 직선)
    t > T : score_at_target × 2^(−(t − T) / h)                  (기본 85 × 2^(−(t−T)/h), h분마다 절반)

    0분 = 100점, T분 = 85점. 0에 가까워지기만 하고 0이 되지는 않는다.
    """
    if t < 0:
        raise ValueError(f"시간은 0 이상이어야 합니다: {t}")
    if target <= 0 or half_life <= 0:
        raise ValueError(f"기준 시간과 반감기는 0보다 커야 합니다: T={target}, h={half_life}")
    if t <= target:
        return cfg.score_max - (cfg.score_max - cfg.score_at_target) * t / target
    return cfg.score_at_target * 2 ** (-(t - target) / half_life)


# ---------------------------------------------------------------- 2장 예산 필터 · 4.3장 주거비 점수

def monthly_fixed_cost(p: Property, cfg: ScoringConfig = CONFIG) -> float | None:
    """월 고정 주거비 = 월세 + 관리비 (만원). 둘 중 하나라도 없으면 None.

    관리비가 없는 매물의 처리는 설계안에서 보류라, 지금은 계산하지 않고 제외한다.
    """
    if p.monthly_rent_manwon is None or p.maintenance_manwon is None:
        return None
    return _round_half_up(p.monthly_rent_manwon + p.maintenance_manwon, cfg.money_decimals)


def budget_exclusion_reason(
    p: Property,
    budget_manwon: float,
    deposit_budget_manwon: float | None = None,
    cfg: ScoringConfig = CONFIG,
) -> str | None:
    """필수 조건(2장)에 걸리면 제외 사유, 통과하면 None.

    - 월세 + 관리비 > 월 예산 + 여유(지금 0) → 제외
    - 보증금 > 보증금 예산 × (1 + 여유, 지금 0) → 제외 (보증금 예산을 입력한 경우만)
    """
    cost = monthly_fixed_cost(p, cfg)
    if cost is None:
        return "월세 또는 관리비 정보 없음"
    if cost > budget_manwon + cfg.monthly_budget_slack_manwon:
        over = _round_half_up(cost - budget_manwon, cfg.money_decimals)
        return f"예산보다 월 {_fmt(over)}만 원 비싸서 제외"

    if deposit_budget_manwon is not None:
        if p.deposit_manwon is None:
            return "보증금 정보 없음"
        if p.deposit_manwon > deposit_budget_manwon * (1 + cfg.deposit_budget_slack_ratio):
            over = _round_half_up(p.deposit_manwon - deposit_budget_manwon, cfg.money_decimals)
            return f"보증금이 예산보다 {_fmt(over)}만 원 많아서 제외"
    return None


def is_over_budget(monthly_cost: float, budget_manwon: float) -> bool:
    """예산 ~ 예산 + 여유 사이: 추천에는 넣고 '예산 초과' 표시."""
    return monthly_cost > budget_manwon


def cost_score(monthly_cost: float, lowest: float, highest: float, cfg: ScoringConfig = CONFIG) -> float:
    """주거비 점수 (2026-11-08 결정): 필터를 통과한 매물 안에서 상대 비교.

    가장 싼 매물(lowest) = 100점, 가장 비싼 매물(highest) = 0점, 사이는 직선
    (돈은 1만 원 차이가 어느 구간에서나 비슷하게 느껴지므로). 모두 같은 값이면 100점.
    예산은 점수에 쓰지 않는다 (예산은 1차 필터로만).
    """
    if highest < lowest:
        raise ValueError(f"최고가가 최저가보다 작습니다: lowest={lowest}, highest={highest}")
    if highest == lowest:
        return cfg.score_max
    raw = cfg.score_max * (highest - monthly_cost) / (highest - lowest)
    return min(cfg.score_max, max(0.0, raw))


# ---------------------------------------------------------------- 시간표 입력 (criteria.py 에서 옮김)

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
    """시간표 한 칸 [요일, 시작, 종료, 강의동]. 시간표 사진 인식(10장 8단계)도 이 모양으로 뽑는다."""

    day: Day
    start: str
    end: str
    building: str  # CSV spotN_name 과 같은 이름 (예: "공과대학", "ECC")

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


# ---------------------------------------------------------------- 이동시간 데이터

# 매물 ID → 목적지(강의동) 이름 → 도보시간(분)
TravelTimes = dict[str, dict[str, float]]


def travel_times_from_csv(properties: Iterable[Property]) -> TravelTimes:
    """CSV 의 spot1~4 도보시간으로 강의동 이동시간 표를 만든다 (criteria.py 에서 옮김)."""
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


# ---------------------------------------------------------------- 4.1장 이동시간 (부분별 편도 평균)

def department_avg_walk(times: Mapping[str, float], buildings: Iterable[str]) -> float | None:
    """학과 건물 편도 평균 (여러 건물이면 평균). 건물이 없거나 도보시간을 모르면 None."""
    buildings = list(dict.fromkeys(buildings))
    if not buildings or missing_destinations(times, buildings):
        return None
    return sum(times[b] for b in buildings) / len(buildings)


def timetable_avg_walk(times: Mapping[str, float], timetable: list[ClassSlot]) -> float | None:
    """시간표 편도 평균. 요일마다 집 → 첫 수업 건물, 마지막 수업 건물 → 집 (수업 사이 이동은 제외).

    기존 criteria.weekly_commute_min 의 주간 합계를 편도 횟수(요일 수 × 2)로 나눈 값.
    시간표가 없거나 도보시간을 모르는 건물이 있으면 None.
    """
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


TravelPartKey = Literal["department", "frequent", "timetable"]
TRAVEL_PART_LABELS: dict[str, str] = {"department": "학과 건물", "frequent": "자주 가는 곳", "timetable": "시간표"}


class TravelPart(BaseModel):
    avg_min: float     # 편도 평균 (분)
    target_min: float  # 이 부분의 T
    score: float       # 0~100
    weight: float      # 이동시간 안에서 실제로 쓴 비중 (입력 없는 부분을 나눈 뒤)


class TravelScore(BaseModel):
    score: float
    parts: dict[str, TravelPart]


def travel_part_weights(available: Iterable[str], cfg: ScoringConfig = CONFIG) -> dict[str, float]:
    """이동시간 내부 비중 (학과 0.4 / 자주 가는 곳 0.4 / 시간표 0.2).

    입력·데이터가 없는 부분의 비중은 나머지 부분에 비율대로 나눈다.
    """
    base = {
        "department": cfg.travel_weight_department,
        "frequent": cfg.travel_weight_frequent,
        "timetable": cfg.travel_weight_timetable,
    }
    kept = {k: w for k, w in base.items() if k in set(available)}
    total = sum(kept.values())
    if total <= 0:
        return {}
    return {k: w / total for k, w in kept.items()}


def travel_score(
    desired_commute_min: float,
    *,
    department_avg_min: float | None = None,
    timetable_avg_min: float | None = None,
    frequent_avg_min: float | None = None,
    cfg: ScoringConfig = CONFIG,
) -> TravelScore | None:
    """이동시간 점수 = Σ 부분 비중 × 부분 점수. 부분 점수는 편도 평균을 3장 함수로 점수화한 것.

    - 학과 건물·시간표: T = 희망 통학 시간, h = 10 (도보)
    - 자주 가는 곳: T = 20, h = 15 (편도 평균 계산은 10장 7단계에서 붙인다)
    값이 None 인 부분은 비중을 나머지에 나눈다. 모든 부분이 None 이면 None.
    """
    specs = {
        "department": (department_avg_min, desired_commute_min, cfg.department_half_life_min),
        "frequent": (frequent_avg_min, cfg.frequent_target_min, cfg.frequent_half_life_min),
        "timetable": (timetable_avg_min, desired_commute_min, cfg.timetable_half_life_min),
    }
    available = [k for k, (avg, _, _) in specs.items() if avg is not None]
    weights = travel_part_weights(available, cfg)
    if not weights:
        return None
    parts: dict[str, TravelPart] = {}
    for key, w in weights.items():
        avg, target, half_life = specs[key]
        assert avg is not None
        parts[key] = TravelPart(
            avg_min=avg, target_min=target, score=time_score(avg, target, half_life, cfg), weight=w
        )
    return TravelScore(score=sum(p.weight * p.score for p in parts.values()), parts=parts)


# ---------------------------------------------------------------- 1장 입력

Factor = Literal["travel", "transit", "cost"]
FACTORS: tuple[Factor, ...] = ("travel", "transit", "cost")
FACTOR_LABELS: dict[str, str] = {"travel": "이동시간", "transit": "교통 편의성", "cost": "주거비"}


class RecommendRequest(BaseModel):
    """POST /recommend 입력 (설계안 1장 중 지금 계산할 수 있는 항목).

    자주 가는 곳(지도)은 10장 7단계, 시간표 사진 인식은 8단계에서 붙인다.
    """

    budget_manwon: Annotated[float, Field(gt=0, description="월 고정 주거비 예산 (월세 + 관리비, 만원)")]
    deposit_budget_manwon: Annotated[
        float | None, Field(ge=0, description="보증금 예산 (만원). 없으면 보증금 필터 생략")
    ] = None
    department: Annotated[str, Field(min_length=1)]
    department_buildings: Annotated[
        list[str] | None,
        Field(min_length=1, description="학과 건물. 비우면 학과 → 건물 표로 채운다 (사용자가 고칠 수 있음)"),
    ] = None
    grade: Annotated[int, Field(ge=1, description="학년. MVP 에서는 받기만 하고 학과 건물 비중은 고정")]
    desired_commute_min: Annotated[float, Field(gt=0, description="희망 통학 시간 (편도, 도보 기준, 분)")]
    timetable: list[ClassSlot] = Field(default_factory=list)
    priorities: tuple[Factor, Factor] = Field(description="1순위, 2순위. 3순위는 자동")

    @field_validator("priorities")
    @classmethod
    def _distinct_priorities(cls, v: tuple[str, str]) -> tuple[str, str]:
        if v[0] == v[1]:
            raise ValueError("1순위와 2순위는 서로 달라야 합니다.")
        return v

    @model_validator(mode="after")
    def _department_buildings_known(self) -> "RecommendRequest":
        if self.department_buildings is None and self.department not in DEPARTMENT_BUILDINGS:
            raise ValueError(
                f"학과 건물을 알 수 없는 학과입니다: {self.department!r}. department_buildings 로 직접 입력해 주세요."
            )
        return self

    def resolved_department_buildings(self) -> list[str]:
        if self.department_buildings is not None:
            return list(dict.fromkeys(self.department_buildings))
        return list(DEPARTMENT_BUILDINGS[self.department])


class UnknownBuildingError(ValueError):
    """이동시간 데이터에 없는 건물 이름 (API 에서는 422)."""

    def __init__(self, names: list[str], known: list[str]):
        self.names = names
        super().__init__(f"데이터에 없는 건물입니다: {', '.join(names)} (가능: {', '.join(known)})")


# ---------------------------------------------------------------- 5장 종합 점수

def full_priorities(priorities: tuple[str, str]) -> tuple[str, str, str]:
    """1·2순위 → 1·2·3순위 (3순위는 남은 요소)."""
    third = next(f for f in FACTORS if f not in priorities)
    return (priorities[0], priorities[1], third)


def rank_weights(priorities: tuple[str, str], cfg: ScoringConfig = CONFIG) -> dict[str, float]:
    """1·2·3순위 → 0.55 / 0.30 / 0.15."""
    p1, p2, p3 = full_priorities(priorities)
    return {p1: cfg.weight_rank1, p2: cfg.weight_rank2, p3: cfg.weight_rank3}


def applied_weights(base: Mapping[str, float], available: Iterable[str]) -> dict[str, float]:
    """계산할 수 없는 요소의 비중을 나머지 요소에 비율대로 나눈다. 모든 요소 키를 돌려준다 (못 쓰면 0)."""
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
    over_budget: bool


def total_score(c: Candidate, weights: Mapping[str, float]) -> float:
    return sum(weights[f] * s for f, s in c.factor_scores.items() if s is not None and weights[f] > 0)


def rank_candidates(candidates: Iterable[Candidate], weights: Mapping[str, float]) -> list[Candidate]:
    """종합 점수 높은 순, 동점이면 매물 ID 순."""
    return sorted(candidates, key=lambda c: (-total_score(c, weights), c.property_id))


# ---------------------------------------------------------------- 출력 모델

class RankedProperty(BaseModel):
    rank: int
    property_id: str
    score: float                            # 종합 점수 0~100
    factor_scores: dict[str, float | None]  # 요소별 절대 점수 (계산 못 한 요소는 null). FE 가 순위를 바꿔 다시 정렬할 때 쓴다
    contributions: dict[str, float]         # 요소별 가중치 × 점수 (합 = score)
    travel_parts: dict[str, TravelPart]     # 이동시간 세부 부분 (학과 건물, 시간표)
    monthly_cost_manwon: float
    deposit_manwon: float | None
    over_budget: bool                       # 예산 ~ 예산 + 여유 구간 → "예산 초과" 표시


class Reason(BaseModel):
    """비추천 사유의 근거 한 요소. 숫자는 코드가 만들고 LLM 은 문장만 다듬는다."""

    factor: str
    label: str
    part: str | None = None          # 이동시간이면 점수가 가장 낮은 세부 부분 (department / timetable / frequent)
    part_label: str | None = None
    value: float | None = None       # 이동시간: 그 부분의 편도(분), 주거비: 월 고정 주거비(만원)
    top1_value: float | None = None  # 지금 1위 매물의 같은 값
    target: float | None = None      # 이동시간: 그 부분의 T(분), 주거비: 예산(만원)
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
    weights: dict[str, float]  # 실제로 쓴 가중치 (반올림 없음, 계산 못 한 요소는 0)
    ranked: list[RankedProperty]
    not_recommended: list[NotRecommended]
    excluded: list[Excluded]
    assumptions: dict[str, Any]
    notes: list[str]


# ---------------------------------------------------------------- 6장 비추천 사유

def _r(value: float | None, digits: int) -> float | None:
    return None if value is None else _round_half_up(value, digits)


def weakest_travel_part(travel: TravelScore) -> tuple[str, TravelPart]:
    """이동시간 세부 부분 중 점수가 가장 낮은 부분 (동점이면 학과 건물 → 자주 가는 곳 → 시간표 순)."""
    order = list(TRAVEL_PART_LABELS)
    return min(travel.parts.items(), key=lambda kv: (kv[1].score, order.index(kv[0])))


def _factor_reason(
    factor: str, c: Candidate, top1: Candidate, weights: Mapping[str, float], budget: float, cfg: ScoringConfig
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
        reason = reason.model_copy(update={
            "part": key,
            "part_label": TRAVEL_PART_LABELS[key],
            "value": _r(part.avg_min, cfg.minutes_decimals),
            "top1_value": _r(top1_part.avg_min if top1_part else None, cfg.minutes_decimals),
            "target": part.target_min,
            "unit": "분",
        })
    elif factor == "cost":
        reason = reason.model_copy(update={
            "value": c.monthly_cost, "top1_value": top1.monthly_cost, "target": budget, "unit": "만원",
        })
    return reason


_STRONG_PREFIX = {
    "travel": "1위 매물보다 가깝지만 ",
    "cost": "1위 매물보다 저렴하지만 ",
    "transit": "1위 매물보다 역이 가깝지만 ",
}
_TRAVEL_WHERE = {
    "department": "학과 건물까지 편도",
    "timetable": "시간표 기준 편도 평균",
    "frequent": "자주 가는 곳까지 편도 평균",
}


def _weak_sentence(r: Reason, over_budget: bool, cfg: ScoringConfig) -> str:
    """약한 요소 문장. 숫자는 Reason 의 값을 그대로 쓴다."""
    m, money = cfg.minutes_decimals, cfg.money_decimals
    fallback = f"{r.label} 점수가 1위 매물보다 {_fmt(_round_half_up(r.top1_score - r.score, cfg.score_decimals))}점 낮아요."
    if r.factor == "travel" and r.value is not None and r.target is not None:
        where = _TRAVEL_WHERE[r.part or "department"]
        target_name = "기준 시간" if r.part == "frequent" else "희망 통학 시간"
        if r.value > r.target:
            diff = _round_half_up(r.value - r.target, m)
            return f"{where} {_fmt(r.value)}분이라 {target_name}({_fmt(r.target)}분)보다 {_fmt(diff)}분 길어요."
        if r.top1_value is not None and r.value > r.top1_value:
            diff = _round_half_up(r.value - r.top1_value, m)
            return f"{where} {_fmt(r.value)}분으로 1위 매물({_fmt(r.top1_value)}분)보다 {_fmt(diff)}분 더 걸려요."
        return f"{fallback} ({where} {_fmt(r.value)}분)"
    if r.factor == "cost" and r.value is not None and r.target is not None:
        if over_budget:
            diff = _round_half_up(r.value - r.target, money)
            return f"월 고정 주거비가 {_fmt(r.value)}만 원으로 예산({_fmt(r.target)}만 원)보다 {_fmt(diff)}만 원 비싸요."
        if r.top1_value is not None and r.value > r.top1_value:
            diff = _round_half_up(r.value - r.top1_value, money)
            return f"월 고정 주거비가 {_fmt(r.value)}만 원으로 1위 매물({_fmt(r.top1_value)}만 원)보다 {_fmt(diff)}만 원 비싸요."
    return fallback


def not_recommended(
    candidates: list[Candidate],
    priorities: tuple[str, str],
    available: Iterable[str],
    budget: float,
    cfg: ScoringConfig = CONFIG,
) -> list[NotRecommended]:
    """다른 5가지 1·2순위 조합 중 어디서든 상위 K위 안인데, 지금 조합 상위 N위(화면) 밖인 매물.

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
        weak = _factor_reason(weak_f, c, top1, weights, budget, cfg)
        strong = _factor_reason(strong_f, c, top1, weights, budget, cfg) if gaps[strong_f] < 0 else None
        message = (_STRONG_PREFIX[strong_f] if strong else "") + _weak_sentence(weak, c.over_budget, cfg)
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
    cfg: ScoringConfig = CONFIG,
) -> RecommendResult:
    """입력 → 필터 → 요소별 절대 점수 → 1·2순위 가중합 → 순위 + 비추천 사유 + assumptions."""
    properties = list(properties)
    travel = travel if travel is not None else travel_times_from_csv(properties)
    notes: list[str] = []
    excluded: list[Excluded] = []

    dept_buildings = req.resolved_department_buildings()
    needed = list(dict.fromkeys(dept_buildings + [s.building for s in req.timetable]))
    if properties:
        known = sorted({name for row in travel.values() for name in row})
        unknown = [b for b in needed if b not in known]
        if unknown:
            raise UnknownBuildingError(unknown, known)

    # 이동시간 부분: 학과 건물은 항상, 시간표는 입력했을 때, 자주 가는 곳은 아직 없음 (10장 7단계)
    parts_available = ["department"] + (["timetable"] if req.timetable else [])
    part_weights = travel_part_weights(parts_available, cfg)
    notes.append("자주 가는 곳은 아직 계산할 수 없어 이동시간 안의 비중을 다른 부분에 비율대로 나눴습니다.")
    if not req.timetable:
        notes.append("시간표를 입력하지 않아 이동시간 안의 시간표 비중을 학과 건물에 나눴습니다.")

    # 1) 필터 + 요소별 점수
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for p in properties:
        if p.property_id in seen:
            excluded.append(Excluded(property_id=p.property_id, reason="매물 ID 중복"))
            continue
        seen.add(p.property_id)

        reason = budget_exclusion_reason(p, req.budget_manwon, req.deposit_budget_manwon, cfg)
        if reason:
            excluded.append(Excluded(property_id=p.property_id, reason=reason))
            continue
        times = travel.get(p.property_id, {})
        missing = missing_destinations(times, needed)
        if missing:
            excluded.append(Excluded(property_id=p.property_id, reason=f"도보시간 정보 없음: {', '.join(missing)}"))
            continue

        cost = monthly_fixed_cost(p, cfg)
        assert cost is not None  # budget_exclusion_reason 에서 걸렀다
        ts = travel_score(
            req.desired_commute_min,
            department_avg_min=department_avg_walk(times, dept_buildings),
            timetable_avg_min=timetable_avg_walk(times, req.timetable),
            cfg=cfg,
        )
        candidates.append(Candidate(
            property_id=p.property_id,
            factor_scores={
                "travel": ts.score if ts else None,
                "transit": None,  # 지하철역 도보시간 데이터가 생기면 10장 6단계에서 채운다
                "cost": None,  # 필터 통과 매물이 다 모인 뒤 상대 비교로 채운다
            },
            travel=ts,
            monthly_cost=cost,
            deposit=p.deposit_manwon,
            over_budget=is_over_budget(cost, req.budget_manwon),
        ))

    # 주거비 점수: 필터 통과 매물 안 상대 비교 (예산은 점수에 쓰지 않는다)
    cost_range = (min(c.monthly_cost for c in candidates), max(c.monthly_cost for c in candidates)) if candidates else None
    if cost_range:
        for c in candidates:
            c.factor_scores["cost"] = cost_score(c.monthly_cost, *cost_range, cfg)

    # 2) 가중치: 1·2순위 → 0.55/0.30/0.15, 계산 못 한 요소의 비중은 나머지에 나눈다
    order = full_priorities(req.priorities)
    base = rank_weights(req.priorities, cfg)
    available = [f for f in FACTORS if candidates and all(c.factor_scores[f] is not None for c in candidates)]
    weights = applied_weights(base, available)
    for f in FACTORS:
        if candidates and f not in available:
            label = FACTOR_LABELS[f]
            if f in req.priorities:
                notes.append(
                    f"{label}은 아직 계산할 수 없어 선택한 순위가 반영되지 않았습니다. "
                    f"{label}의 비중은 나머지 요소에 비율대로 나눴습니다."
                )
            else:
                notes.append(f"{label}은 아직 계산할 수 없어 비중을 나머지 요소에 비율대로 나눴습니다.")

    # 3) 순위 (응답에 넣을 때만 반올림)
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
            monthly_cost_manwon=c.monthly_cost,
            deposit_manwon=c.deposit,
            over_budget=c.over_budget,
        )
        for i, c in enumerate(rank_candidates(candidates, weights)[: cfg.result_limit], start=1)
    ]
    if len(ranked) < cfg.displayed_top_n:
        notes.append(f"조건에 맞는 매물이 {len(ranked)}개뿐입니다.")

    return RecommendResult(
        priorities=order,
        weights=weights,
        ranked=ranked,
        not_recommended=not_recommended(candidates, req.priorities, available, req.budget_manwon, cfg),
        excluded=excluded,
        assumptions={
            "priorities": list(order),
            "weights_by_rank": base,
            "weights_applied": weights,
            "unavailable_factors": [f for f in FACTORS if f not in available],
            "travel_part_weights_applied": part_weights,
            "unavailable_travel_parts": [k for k in TRAVEL_PART_LABELS if k not in part_weights],
            "time_score": {
                "department": {"T": req.desired_commute_min, "h": cfg.department_half_life_min, "mode": "도보"},
                "timetable": {"T": req.desired_commute_min, "h": cfg.timetable_half_life_min, "mode": "도보"},
                "frequent": {"T": cfg.frequent_target_min, "h": cfg.frequent_half_life_min},
                "transit": {"T": cfg.subway_target_min, "h": cfg.subway_half_life_min},
            },
            "cost_score": {
                "method": "필터 통과 매물 안 상대 비교 (가장 싼 매물 100점 ~ 가장 비싼 매물 0점)",
                "lowest_manwon": cost_range[0] if cost_range else None,
                "highest_manwon": cost_range[1] if cost_range else None,
            },
            "desired_commute_min": req.desired_commute_min,
            "department": req.department,
            "department_buildings": dept_buildings,
            "department_buildings_source": (
                "입력" if req.department_buildings is not None else "학과 → 건물 예시표 (예시값, 팀 확인 필요)"
            ),
            "grade": req.grade,
            "grade_note": "학년은 받기만 하고 학과 건물 비중은 고정입니다 (설계안 4.1장).",
            "budget_manwon": req.budget_manwon,
            "monthly_limit_manwon": req.budget_manwon + cfg.monthly_budget_slack_manwon,
            "deposit_budget_manwon": req.deposit_budget_manwon,
            "deposit_limit_manwon": (
                None if req.deposit_budget_manwon is None
                else _round_half_up(req.deposit_budget_manwon * (1 + cfg.deposit_budget_slack_ratio), cfg.money_decimals)
            ),
            "config": asdict(cfg),
        },
        notes=notes,
    )
