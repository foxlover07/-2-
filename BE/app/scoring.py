"""추천 점수 v2 (설계안 recommend-score-design.md).

요소는 이동시간 · 교통 편의성 · 주거비 3개이고, 각 요소는 매물별 절대 점수(0~100)다.
다른 매물에 따라 점수가 바뀌지 않는다 (min-max 정규화를 쓰지 않는다).
숫자는 모두 app/scoring_config.py 에서 읽는다. 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
"""

import math
import re
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, field_validator

from app.models import Property
from app.scoring_config import CONFIG, ScoringConfig


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

    - 월세 + 관리비 > 월 예산 + 여유(10만 원) → 제외
    - 보증금 > 보증금 예산 × (1 + 여유 20%) → 제외 (보증금 예산을 입력한 경우만)
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


def cost_score(monthly_cost: float, budget_manwon: float, cfg: ScoringConfig = CONFIG) -> float:
    """주거비 점수 = 100 × (예산 − 월 고정 주거비) ÷ (예산 × 0.4) → 0~100 으로 자른다.

    예산의 60% 이하면 100점, 예산과 같으면 0점, 여유 구간(예산 초과)도 0점.
    """
    if budget_manwon <= 0:
        raise ValueError(f"예산은 0보다 커야 합니다: {budget_manwon}")
    span = budget_manwon * (1 - cfg.cost_full_score_ratio)
    raw = cfg.score_max * (budget_manwon - monthly_cost) / span
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
