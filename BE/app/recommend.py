"""추천 점수 계산 (가중합 MCDM).

LLM 없이 순수 계산만 한다. 입력은 매물 목록과 FE 의 Profile 과 같은 모양의 프로필이고,
출력은 순위·점수·기준별 기여도다.

기준은 둘이며, 둘 다 낮을수록 좋다.
  - cost: 월 고정 주거비 = 월세 + 관리비 (FE utils/cost.ts 와 같은 계산)
  - walk: 가중 평균 도보시간 = Σ(도보시간 × 주간 방문 횟수) ÷ Σ(방문 횟수) (FE utils/walk.ts 와 같은 계산)

점수 계산:
  1. 기준별 값을 0~1 로 맞춘다 (min-max, 낮을수록 1 에 가깝다).
  2. 점수 = 주거비 가중치 × 주거비 점수 + 이동 가중치 × 이동 점수
  3. 기여도 = 기준별 (가중치 × 점수). 두 기여도를 더하면 점수가 된다.

아래 값들은 팀 논의 전의 임의 기본값이다. 규칙이 정해지면 해당 함수·상수만 고친다.
  - IMPORTANCE 1~5 → 가중치 변환: importance_to_weights() (지금은 비율)
  - 예산 처리: budget_mode ("exclude" 탈락 / "penalty" 감점 / "ignore" 무시), budget_penalty
  - 0~1 맞추는 범위: 점수를 매길 수 있는 매물 전체 (예산 탈락 전). 예산을 바꿔도 점수가 변하지 않는다.
  - 모든 매물의 값이 같으면 그 기준은 전부 1.0
  - 방문 강의동이 하나도 없으면 이동 기준을 끄고 주거비만으로 계산한다 (notes 에 남긴다)
  - 어떤 매물에도 없는 강의동 이름은 버린다 (notes 에 남긴다)
  - penalty 모드에서는 점수가 음수가 될 수 있다 (순위 비교용 값이라 그대로 둔다)
"""

import math
from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.models import Property

IMPORTANCE_MIN = 1
IMPORTANCE_MAX = 5

BudgetMode = Literal["exclude", "penalty", "ignore"]
DEFAULT_BUDGET_MODE: BudgetMode = "exclude"
DEFAULT_BUDGET_PENALTY = 0.2
DEFAULT_TOP_N = 3

Importance = Annotated[int, Field(ge=IMPORTANCE_MIN, le=IMPORTANCE_MAX)]


# ---------------------------------------------------------------- 입력·출력 모델

class Profile(BaseModel):
    """FE/src/contexts/ProfileContext.tsx 의 Profile 과 같은 모양 (snake_case)."""

    visits: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    budget_manwon: Annotated[float, Field(ge=0)] | None = None
    cost_importance: Importance = 3
    walk_importance: Importance = 3


class Weights(BaseModel):
    cost: Annotated[float, Field(ge=0)]
    walk: Annotated[float, Field(ge=0)]

    def normalized(self) -> "Weights":
        """두 가중치의 합이 1 이 되도록 비율을 맞춘다."""
        total = self.cost + self.walk
        if total <= 0:
            raise ValueError("가중치의 합이 0 입니다. 계산할 수 있는 기준이 없습니다.")
        return Weights(cost=self.cost / total, walk=self.walk / total)


class Contribution(BaseModel):
    cost: float
    walk: float


class ScoredProperty(BaseModel):
    rank: int
    property_id: str
    score: float
    contributions: Contribution
    penalty: float
    monthly_cost_manwon: float
    avg_walk_min: float | None
    over_budget: bool | None


class ExcludedProperty(BaseModel):
    property_id: str
    reason: str


class RecommendResult(BaseModel):
    weights: Weights  # 실제로 적용된 가중치 (합 1, 꺼진 기준은 0)
    top: list[ScoredProperty]
    ranked: list[ScoredProperty]  # 점수를 매긴 전체 순위
    excluded: list[ExcludedProperty]
    notes: list[str]


# ---------------------------------------------------------------- 가중치

def importance_to_weights(cost_importance: int, walk_importance: int) -> Weights:
    """중요도(1~5) 두 개를 합이 1 인 가중치로 바꾼다. 지금은 단순 비율.

    예: (3, 3) → 0.5 / 0.5,  (5, 1) → 약 0.83 / 0.17
    """
    return Weights(cost=cost_importance, walk=walk_importance).normalized()


# ---------------------------------------------------------------- 기준 값 계산 (FE 와 같은 규칙)

def _round_half_up(value: float, digits: int) -> float:
    """JS Math.round 처럼 .5 를 올린다 (파이썬 round 는 짝수로 반올림하므로 쓰지 않는다)."""
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def monthly_fixed_cost(p: Property) -> float | None:
    """월 고정 주거비 = 월세 + 관리비 (만원). 둘 중 하나라도 없으면 None."""
    if p.monthly_rent_manwon is None or p.maintenance_manwon is None:
        return None
    return _round_half_up(p.monthly_rent_manwon + p.maintenance_manwon, 2)


def _spots(p: Property) -> list[tuple[str, float | None]]:
    pairs = [
        (p.spot1_name, p.spot1_walk_min),
        (p.spot2_name, p.spot2_walk_min),
        (p.spot3_name, p.spot3_walk_min),
        (p.spot4_name, p.spot4_walk_min),
    ]
    return [(name, walk) for name, walk in pairs if name is not None]


def _walk_min(p: Property, spot_name: str) -> float | None:
    return next((walk for name, walk in _spots(p) if name == spot_name), None)


def weighted_avg_walk(p: Property, visits: dict[str, int]) -> float | None:
    """가중 평균 도보시간 (분, 소수 첫째 자리).

    방문 0회인 강의동은 제외한다. 방문 합이 0 이거나, 방문하는 강의동의
    도보시간이 하나라도 없으면 계산할 수 없으므로 None.
    """
    weighted = 0.0
    total_visits = 0
    for name, count in visits.items():
        if count <= 0:
            continue
        walk = _walk_min(p, name)
        if walk is None:
            return None
        weighted += walk * count
        total_visits += count
    if total_visits == 0:
        return None
    return _round_half_up(weighted / total_visits, 1)


# ---------------------------------------------------------------- 정규화

def lower_is_better(values: dict[str, float]) -> dict[str, float]:
    """min-max 로 0~1 점수를 만든다. 가장 낮은 값이 1, 가장 높은 값이 0.

    모든 값이 같으면 구분할 수 없으므로 전부 1.0.
    """
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {key: 1.0 for key in values}
    return {key: (hi - v) / (hi - lo) for key, v in values.items()}


# ---------------------------------------------------------------- 추천

def recommend(
    properties: Iterable[Property],
    profile: Profile,
    *,
    weights: Weights | None = None,
    top_n: int = DEFAULT_TOP_N,
    budget_mode: BudgetMode = DEFAULT_BUDGET_MODE,
    budget_penalty: float = DEFAULT_BUDGET_PENALTY,
) -> RecommendResult:
    """매물에 점수를 매겨 순위를 돌려준다.

    weights 를 주면 그 값을 쓰고(합이 1 이 아니어도 비율로 맞춘다),
    주지 않으면 프로필의 중요도(1~5)에서 만든다.
    """
    if top_n < 1:
        raise ValueError("top_n 은 1 이상이어야 합니다.")

    properties = list(properties)
    notes: list[str] = []
    excluded: list[ExcludedProperty] = []

    base = weights if weights is not None else importance_to_weights(
        profile.cost_importance, profile.walk_importance
    )

    # 어떤 매물에도 없는 강의동 이름(오타 등)은 버린다. 그대로 두면 모든 매물이 탈락한다.
    known_spots = {name for p in properties for name, _ in _spots(p)}
    visits = {name: count for name, count in profile.visits.items() if count > 0 and name in known_spots}
    unknown = sorted(name for name, count in profile.visits.items() if count > 0 and name not in known_spots)
    if unknown:
        notes.append(f"데이터에 없는 강의동은 제외했습니다: {', '.join(unknown)}")

    # 방문 강의동이 없으면 이동 기준을 끈다.
    walk_enabled = bool(visits)
    if not walk_enabled:
        notes.append("방문 강의동이 없어 이동시간 기준을 빼고 주거비만으로 계산했습니다.")
    cost_w = base.cost
    walk_w = base.walk if walk_enabled else 0.0
    if cost_w + walk_w <= 0:
        notes.append("쓸 수 있는 가중치가 없어 주거비만으로 계산했습니다.")
        cost_w = 1.0
    applied = Weights(cost=cost_w, walk=walk_w).normalized()

    # 1) 점수를 매길 수 있는 매물만 고른다 (값이 없으면 계산하지 않고 제외).
    costs: dict[str, float] = {}
    walks: dict[str, float] = {}
    by_id: dict[str, Property] = {}
    for p in properties:
        if p.property_id in by_id:
            excluded.append(ExcludedProperty(property_id=p.property_id, reason="매물 ID 중복"))
            continue
        cost = monthly_fixed_cost(p)
        if cost is None:
            excluded.append(ExcludedProperty(property_id=p.property_id, reason="월세 또는 관리비 정보 없음"))
            continue
        walk = weighted_avg_walk(p, visits) if walk_enabled else None
        if walk_enabled and walk is None:
            excluded.append(
                ExcludedProperty(property_id=p.property_id, reason="방문 강의동의 도보시간 정보 없음")
            )
            continue
        by_id[p.property_id] = p
        costs[p.property_id] = cost
        if walk is not None:
            walks[p.property_id] = walk

    # 2) 기준별 0~1 점수. 예산 탈락 전의 전체 후보 기준으로 맞춘다.
    cost_scores = lower_is_better(costs)
    walk_scores = lower_is_better(walks) if walk_enabled else {}

    # 3) 가중합과 예산 처리.
    scored: list[tuple[float, str, ScoredProperty]] = []
    for pid in by_id:
        cost_contrib = applied.cost * cost_scores[pid]
        walk_contrib = applied.walk * walk_scores[pid] if walk_enabled else 0.0

        over_budget: bool | None = None
        if profile.budget_manwon is not None:
            over_budget = costs[pid] > profile.budget_manwon

        penalty = 0.0
        if over_budget and budget_mode == "exclude":
            excluded.append(ExcludedProperty(property_id=pid, reason="월 예산 초과"))
            continue
        if over_budget and budget_mode == "penalty":
            penalty = budget_penalty

        total = cost_contrib + walk_contrib - penalty
        scored.append(
            (
                total,
                pid,
                ScoredProperty(
                    rank=0,  # 정렬 뒤에 채운다
                    property_id=pid,
                    score=round(total, 4),
                    contributions=Contribution(cost=round(cost_contrib, 4), walk=round(walk_contrib, 4)),
                    penalty=penalty,
                    monthly_cost_manwon=costs[pid],
                    avg_walk_min=walks.get(pid),
                    over_budget=over_budget,
                ),
            )
        )

    # 4) 점수 높은 순, 같으면 매물 ID 순 (FE 정렬과 같은 규칙).
    scored.sort(key=lambda t: (-round(t[0], 9), t[1]))
    ranked = [item.model_copy(update={"rank": i}) for i, (_, _, item) in enumerate(scored, start=1)]

    if len(ranked) < top_n:
        notes.append(f"추천 가능한 매물이 {len(ranked)}개뿐이라 TOP {top_n} 을 채우지 못했습니다.")

    return RecommendResult(
        weights=applied,
        top=ranked[:top_n],
        ranked=ranked,
        excluded=excluded,
        notes=notes,
    )
