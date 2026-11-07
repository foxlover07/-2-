"""추천 점수 v2 (설계안 recommend-score-design.md).

요소는 이동시간 · 교통 편의성 · 주거비 3개이고, 각 요소는 매물별 절대 점수(0~100)다.
다른 매물에 따라 점수가 바뀌지 않는다 (min-max 정규화를 쓰지 않는다).
숫자는 모두 app/scoring_config.py 에서 읽는다. 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
"""

import math

from pydantic import BaseModel

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
