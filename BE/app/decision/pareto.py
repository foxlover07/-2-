"""Pareto Frontier 와 Trade-off 분석 (지시서 4장).

대상은 엔진 필터를 통과한 매물(`ranked`) 전체다. 엔진 순위는 바꾸지 않고 별도 분석으로 돌려준다.

목적 (실제 데이터가 있는 것만):
- 유지비(월세+관리비) 최소 · 보증금 최소 · 편도 통학시간 최소 · 전용면적 최대
- 공강 귀가·생활 동선 부담은 통합안 엔진이 계산하지 않으므로 쓰지 않는다 (unsupported 로 표시).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.decision.engine import EngineContext, summary, travel_min
from app.scoring import RankedProperty, RecommendRequest

Objective = Literal["cost", "deposit", "travel", "area"]
OBJECTIVE_LABELS: dict[str, str] = {"cost": "유지비", "deposit": "보증금", "travel": "편도 통학시간", "area": "전용면적"}
UNSUPPORTED_OBJECTIVES = {
    "gap_home": "공강 귀가 가능성 — 통합안 엔진에 계산 없음",
    "daily_route": "생활 동선 부담 — 자주 가는 곳 이동시간 데이터 없음",
}
# 값이 클수록 좋은 목적 (나머지는 작을수록 좋음)
_MAXIMIZE = {"area"}


def _value(r: RankedProperty, obj: str) -> float | None:
    return {
        "cost": r.monthly_cost_manwon,
        "deposit": r.deposit_manwon,
        "travel": travel_min(r),
        "area": r.area_m2,
    }[obj]


def _key(r: RankedProperty, objs: list[str]) -> tuple[float, ...]:
    """모두 '작을수록 좋음'으로 맞춘 값."""
    return tuple(-v if o in _MAXIMIZE else v for o in objs for v in [_value(r, o)])  # type: ignore[operator]


def dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


class ParetoItem(BaseModel):
    property_id: str
    rank: int
    score: float
    monthly_cost_manwon: float
    deposit_manwon: float | None
    travel_min: float | None
    area_m2: float
    over_budget: bool
    pareto: bool
    dominated_by: str | None = None  # 이 매물을 지배하는 매물 하나 (모든 목적에서 같거나 낫고 하나 이상 나음)


class ParetoResult(BaseModel):
    objectives: list[str]
    objective_labels: dict[str, str]
    skipped_objectives: dict[str, str]   # 요청했지만 쓰지 못한 목적과 이유
    candidates: int
    frontier: list[ParetoItem]           # 유지비 낮은 순
    dominated_count: int
    top_k: list[ParetoItem]              # 엔진 상위 K 매물의 Pareto 여부
    notes: list[str]


def pareto_analysis(
    ranked: list[RankedProperty], objectives: list[str] | None = None, top_k: int = 3,
) -> ParetoResult:
    asked = list(dict.fromkeys(objectives or ["cost", "deposit", "travel", "area"]))
    skipped: dict[str, str] = {}
    objs: list[str] = []
    for o in asked:
        if o in UNSUPPORTED_OBJECTIVES:
            skipped[o] = UNSUPPORTED_OBJECTIVES[o]
        elif o not in OBJECTIVE_LABELS:
            skipped[o] = "알 수 없는 목적"
        elif ranked and any(_value(r, o) is None for r in ranked):
            skipped[o] = "값이 없는 매물이 있어 이 목적으로는 비교하지 않음"
        else:
            objs.append(o)
    notes: list[str] = []
    if len(objs) < 2:
        notes.append("비교할 목적이 2개 미만이라 Pareto 분석의 의미가 작습니다.")

    keys = {r.property_id: _key(r, objs) for r in ranked} if objs else {r.property_id: () for r in ranked}
    items: list[ParetoItem] = []
    for r in ranked:
        k = keys[r.property_id]
        dom = None
        if objs:
            # 지배하는 매물 중 엔진 순위가 가장 높은 것을 예로 든다
            for other in ranked:
                if other.property_id != r.property_id and dominates(keys[other.property_id], k):
                    dom = other.property_id
                    break
        s = summary(r)
        items.append(ParetoItem(**s, pareto=dom is None, dominated_by=dom))

    frontier = sorted((i for i in items if i.pareto), key=lambda i: (i.monthly_cost_manwon, i.rank))
    top = [i for i in items if i.rank <= top_k]
    for i in top:
        if not i.pareto:
            notes.append(
                f"추천 {i.rank}위 {i.property_id} 는 {i.dominated_by} 보다 모든 목적에서 같거나 불리합니다 "
                "(점수 상한·하한 때문에 같은 점수가 될 수 있음)."
            )
    return ParetoResult(
        objectives=objs,
        objective_labels={o: OBJECTIVE_LABELS[o] for o in objs},
        skipped_objectives=skipped,
        candidates=len(ranked),
        frontier=frontier,
        dominated_count=len(items) - len(frontier),
        top_k=top,
        notes=notes,
    )


# ---------------------------------------------------------------- Trade-off

class Diff(BaseModel):
    """b − a (양수 = b 가 더 큼). 통학은 분, 비용은 만원, 면적은 ㎡."""

    monthly_cost_manwon: float
    deposit_manwon: float | None
    travel_min: float | None
    area_m2: float


def _diff(a: RankedProperty, b: RankedProperty) -> Diff:
    ta, tb = travel_min(a), travel_min(b)
    return Diff(
        monthly_cost_manwon=round(b.monthly_cost_manwon - a.monthly_cost_manwon, 2),
        deposit_manwon=None if a.deposit_manwon is None or b.deposit_manwon is None
        else round(b.deposit_manwon - a.deposit_manwon, 2),
        travel_min=None if ta is None or tb is None else round(tb - ta, 1),
        area_m2=round(b.area_m2 - a.area_m2, 2),
    )


class PayMoreStep(BaseModel):
    extra_budget_manwon: float          # 기준 매물보다 월 최대 이만큼 더 낼 때
    best: dict | None                   # 그 안에서 통학이 가장 짧은 매물
    travel_saved_min: float | None      # 기준 매물보다 줄어드는 편도 시간


class SaveStep(BaseModel):
    label: str
    best: dict | None
    saved_manwon: float | None          # 기준 매물보다 줄어드는 월 유지비
    diff: Diff | None


class TradeoffResult(BaseModel):
    reference: dict                     # 기준 매물 (기본: 추천 1위)
    cheapest: dict | None
    cheapest_vs_reference: Diff | None
    pay_more_for_commute: list[PayMoreStep]
    similar_commute_cheaper: SaveStep
    smaller_area_cheaper: list[SaveStep]
    notes: list[str]


def tradeoff_analysis(
    ranked: list[RankedProperty],
    reference_id: str | None = None,
    extra_steps: tuple[float, ...] = (5, 10, 15, 20),
    commute_tolerance_min: float = 3.0,
    area_steps: tuple[float, ...] = (2, 4),
) -> TradeoffResult | None:
    """비용·통학·면적 차이를 실제 필터 통과 매물로 계산한다. 매물이 없으면 None."""
    if not ranked:
        return None
    by_id = {r.property_id: r for r in ranked}
    ref = by_id.get(reference_id or "", ranked[0])
    notes: list[str] = []
    if reference_id and reference_id not in by_id:
        notes.append(f"{reference_id} 는 조건을 통과한 매물이 아니라 추천 1위를 기준으로 했습니다.")
    ref_t = travel_min(ref)

    cheapest = min(ranked, key=lambda r: (r.monthly_cost_manwon, r.rank))
    steps: list[PayMoreStep] = []
    for extra in extra_steps:
        pool = [r for r in ranked if r.monthly_cost_manwon <= ref.monthly_cost_manwon + extra and travel_min(r) is not None]
        best = min(pool, key=lambda r: (travel_min(r), r.monthly_cost_manwon, r.rank), default=None)
        saved = None if best is None or ref_t is None else round(ref_t - travel_min(best), 1)  # type: ignore[operator]
        steps.append(PayMoreStep(extra_budget_manwon=extra, best=summary(best) if best else None,
                                 travel_saved_min=saved))
    if ranked and max(r.monthly_cost_manwon for r in ranked) < ref.monthly_cost_manwon + max(extra_steps):
        notes.append("더 비싼 매물은 지금 유지비 상한(+여유) 필터에 걸려 분석에 없습니다. 상한을 올린 결과는 조건 완화 분석에서 봅니다.")

    similar = SaveStep(label=f"통학시간이 {commute_tolerance_min:g}분 이내로 비슷하면서 더 싼 매물", best=None,
                       saved_manwon=None, diff=None)
    if ref_t is not None:
        pool = [r for r in ranked if travel_min(r) is not None and travel_min(r) <= ref_t + commute_tolerance_min  # type: ignore[operator]
                and r.monthly_cost_manwon < ref.monthly_cost_manwon]
        b = min(pool, key=lambda r: (r.monthly_cost_manwon, r.rank), default=None)
        if b:
            similar = SaveStep(label=similar.label, best=summary(b),
                               saved_manwon=round(ref.monthly_cost_manwon - b.monthly_cost_manwon, 2), diff=_diff(ref, b))

    area_out: list[SaveStep] = []
    for give in area_steps:
        pool = [r for r in ranked if r.area_m2 >= ref.area_m2 - give and r.monthly_cost_manwon < ref.monthly_cost_manwon]
        b = min(pool, key=lambda r: (r.monthly_cost_manwon, r.rank), default=None)
        area_out.append(SaveStep(
            label=f"면적을 최대 {give:g}㎡ 줄일 때 가장 싼 매물",
            best=summary(b) if b else None,
            saved_manwon=round(ref.monthly_cost_manwon - b.monthly_cost_manwon, 2) if b else None,
            diff=_diff(ref, b) if b else None,
        ))

    return TradeoffResult(
        reference=summary(ref),
        cheapest=summary(cheapest),
        cheapest_vs_reference=None if cheapest.property_id == ref.property_id else _diff(ref, cheapest),
        pay_more_for_commute=steps,
        similar_commute_cheaper=similar,
        smaller_area_cheaper=area_out,
        notes=notes,
    )


class ParetoRequest(BaseModel):
    request: RecommendRequest
    objectives: list[str] | None = None
    top_k: int = Field(3, ge=1, le=20)


class TradeoffRequest(BaseModel):
    request: RecommendRequest
    reference_id: str | None = None
    commute_tolerance_min: float = Field(3.0, ge=0, le=30)


def run_pareto(body: ParetoRequest, ctx: EngineContext | None = None) -> ParetoResult:
    ctx = ctx or EngineContext()
    return pareto_analysis(ctx.run(body.request).ranked, body.objectives, body.top_k)


def run_tradeoff(body: TradeoffRequest, ctx: EngineContext | None = None) -> TradeoffResult | None:
    ctx = ctx or EngineContext()
    return tradeoff_analysis(ctx.run(body.request).ranked, body.reference_id,
                             commute_tolerance_min=body.commute_tolerance_min)
