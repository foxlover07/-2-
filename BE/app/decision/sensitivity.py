"""민감도 분석 (지시서 5장): 가정 조건을 하나씩 바꿔 같은 엔진을 다시 돌리고 순위 변화를 잰다.

산출: TOP 3 유지율, TOP K 변화, Kendall's tau-b · Spearman (공통 매물 기준), 순위 변동 폭, 점수 변화,
조건 종류별 평균 유지율(민감한 조건), 여러 조건에서 상위에 남는 매물(안정 후보).

'안정 후보'는 시나리오 n 개 중 몇 번 상위에 남았는지(횟수)로만 말한다. 확률·신뢰도로 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import permutations

from pydantic import BaseModel, Field

from app.decision.engine import EngineContext
from app.scoring import CONFIG, FACTORS, RecommendRequest, RecommendResult
from app.scoring_config import ScoringConfig

# 순위별 가중치 가정 (기준 0.55/0.30/0.15). 합 1.
WEIGHT_SETS: list[tuple[float, float, float]] = [
    (0.60, 0.30, 0.10),
    (0.50, 0.30, 0.20),
    (0.45, 0.35, 0.20),
    (0.40, 0.35, 0.25),
    (1 / 3, 1 / 3, 1 / 3),
]


# ---------------------------------------------------------------- 순위 비교 지표

def kendall_tau(a: list[str], b: list[str]) -> float | None:
    """두 순위 목록에서 공통 매물의 Kendall's tau (동순위 없음 → tau-b = tau-a). 공통 2개 미만이면 None."""
    common = [x for x in a if x in set(b)]
    n = len(common)
    if n < 2:
        return None
    pos_b = {x: i for i, x in enumerate(x for x in b if x in set(common))}
    seq = [pos_b[x] for x in common]  # a 순서대로 나열한 b 의 위치
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            if seq[i] < seq[j]:
                conc += 1
            else:
                disc += 1
    return round((conc - disc) / (n * (n - 1) / 2), 4)


def spearman_rho(a: list[str], b: list[str]) -> float | None:
    common = [x for x in a if x in set(b)]
    n = len(common)
    if n < 2:
        return None
    ra = {x: i for i, x in enumerate(common)}
    rb = {x: i for i, x in enumerate(x for x in b if x in set(common))}
    d2 = sum((ra[x] - rb[x]) ** 2 for x in common)
    return round(1 - 6 * d2 / (n * (n * n - 1)), 4)


# ---------------------------------------------------------------- 시나리오

class Scenario(BaseModel):
    key: str
    kind: str          # priority / weights / budget / commute / area / larger_area / timetable
    label: str


class ScenarioResult(BaseModel):
    scenario: Scenario
    candidates: int
    top: list[str]                      # 이 시나리오의 상위 K
    top_retention: float | None         # 기준 상위 K 중 이 시나리오 상위 K 에 남은 비율 (0~1)
    kendall_tau: float | None
    spearman: float | None
    max_rank_shift: int | None          # 기준 상위 K 매물의 순위가 가장 많이 바뀐 폭 (빠진 매물은 제외)
    dropped_from_top: list[str]
    entered_top: list[str]
    score_change: dict[str, float]      # 기준 상위 K 매물의 종합 점수 변화 (이 시나리오 − 기준)


class KindSummary(BaseModel):
    kind: str
    label: str
    scenarios: int
    mean_top_retention: float | None
    min_kendall_tau: float | None


class StableCandidate(BaseModel):
    property_id: str
    in_top: int          # 상위 K 에 든 시나리오 수 (기준 포함)
    total: int           # 전체 시나리오 수 (기준 포함)
    baseline_rank: int | None


class SensitivityResult(BaseModel):
    top_k: int
    baseline_top: list[str]
    baseline_candidates: int
    scenarios: list[ScenarioResult]
    by_kind: list[KindSummary]          # 평균 유지율 낮은 순 = 민감한 조건부터
    stable: list[StableCandidate]
    engine_calls: int
    notes: list[str]


KIND_LABELS = {
    "priority": "우선순위 순서", "weights": "순위별 가중치", "budget": "유지비 상한",
    "commute": "희망 통학 시간", "area": "희망 면적", "larger_area": "넓은 면적 선호", "timetable": "시간표",
}


def _scenarios(req: RecommendRequest) -> list[tuple[Scenario, RecommendRequest, ScoringConfig]]:
    out: list[tuple[Scenario, RecommendRequest, ScoringConfig]] = []
    for p in permutations(FACTORS, 2):
        if p != tuple(req.priorities):
            out.append((Scenario(key=f"priority:{p[0]}>{p[1]}", kind="priority", label=f"1·2순위 = {p[0]} → {p[1]}"),
                        req.model_copy(update={"priorities": p}), CONFIG))
    for w in WEIGHT_SETS:
        cfg = replace(CONFIG, weight_rank1=w[0], weight_rank2=w[1], weight_rank3=w[2])
        out.append((Scenario(key=f"weights:{w[0]:.2f}/{w[1]:.2f}/{w[2]:.2f}", kind="weights",
                             label=f"가중치 {w[0]:.2f} / {w[1]:.2f} / {w[2]:.2f}"), req, cfg))
    for d in (-10, -5, 5, 10):
        b = req.budget_manwon + d
        if b > 0:
            out.append((Scenario(key=f"budget:{d:+g}", kind="budget", label=f"유지비 상한 {d:+g}만 원 ({b:g}만 원)"),
                        req.model_copy(update={"budget_manwon": b}), CONFIG))
    for d in (-5, 5):
        c = req.desired_commute_min + d
        if c > 0:
            out.append((Scenario(key=f"commute:{d:+g}", kind="commute", label=f"희망 통학 {d:+g}분 ({c:g}분)"),
                        req.model_copy(update={"desired_commute_min": c}), CONFIG))
    for d in (-2, 2):
        a = req.desired_area_m2 + d
        if a > 0:
            out.append((Scenario(key=f"area:{d:+g}", kind="area", label=f"희망 면적 {d:+g}㎡ ({a:g}㎡)"),
                        req.model_copy(update={"desired_area_m2": a}), CONFIG))
    for la in ("like", "neutral", "dislike"):
        if la != req.larger_area:
            out.append((Scenario(key=f"larger_area:{la}", kind="larger_area", label=f"넓은 면적 선호 = {la}"),
                        req.model_copy(update={"larger_area": la}), CONFIG))
    if req.timetable:
        out.append((Scenario(key="timetable:none", kind="timetable", label="시간표 없이 (학과 건물만)"),
                    req.model_copy(update={"timetable": []}), CONFIG))
    return out


def _compare(base: RecommendResult, other: RecommendResult, sc: Scenario, k: int) -> ScenarioResult:
    b_ids = [r.property_id for r in base.ranked]
    o_ids = [r.property_id for r in other.ranked]
    b_top, o_top = b_ids[:k], o_ids[:k]
    o_rank = {r.property_id: r.rank for r in other.ranked}
    o_score = {r.property_id: r.score for r in other.ranked}
    shifts = [abs(o_rank[r.property_id] - r.rank) for r in base.ranked[:k] if r.property_id in o_rank]
    return ScenarioResult(
        scenario=sc,
        candidates=len(o_ids),
        top=o_top,
        top_retention=round(len(set(b_top) & set(o_top)) / len(b_top), 4) if b_top else None,
        kendall_tau=kendall_tau(b_ids, o_ids),
        spearman=spearman_rho(b_ids, o_ids),
        max_rank_shift=max(shifts) if shifts else None,
        dropped_from_top=[x for x in b_top if x not in o_top],
        entered_top=[x for x in o_top if x not in b_top],
        score_change={r.property_id: round(o_score[r.property_id] - r.score, 2)
                      for r in base.ranked[:k] if r.property_id in o_score},
    )


class SensitivityRequest(BaseModel):
    request: RecommendRequest
    top_k: int = Field(3, ge=1, le=10)
    kinds: list[str] | None = None  # 일부 조건만 (없으면 전부)


def run_sensitivity(body: SensitivityRequest, ctx: EngineContext | None = None) -> SensitivityResult:
    ctx = ctx or EngineContext()
    k = body.top_k
    base = ctx.run(body.request)
    results: list[ScenarioResult] = []
    for sc, req, cfg in _scenarios(body.request):
        if body.kinds and sc.kind not in body.kinds:
            continue
        results.append(_compare(base, ctx.run(req, cfg), sc, k))

    by_kind: list[KindSummary] = []
    for kind in dict.fromkeys(r.scenario.kind for r in results):
        rs = [r for r in results if r.scenario.kind == kind]
        rets = [r.top_retention for r in rs if r.top_retention is not None]
        taus = [r.kendall_tau for r in rs if r.kendall_tau is not None]
        by_kind.append(KindSummary(
            kind=kind, label=KIND_LABELS.get(kind, kind), scenarios=len(rs),
            mean_top_retention=round(sum(rets) / len(rets), 4) if rets else None,
            min_kendall_tau=min(taus) if taus else None,
        ))
    by_kind.sort(key=lambda s: (s.mean_top_retention if s.mean_top_retention is not None else 2, s.kind))

    base_top = [r.property_id for r in base.ranked[:k]]
    counts: dict[str, int] = {x: 1 for x in base_top}
    for r in results:
        for x in r.top:
            counts[x] = counts.get(x, 0) + 1
    base_rank = {r.property_id: r.rank for r in base.ranked}
    total = len(results) + 1
    stable = sorted(
        (StableCandidate(property_id=x, in_top=n, total=total, baseline_rank=base_rank.get(x)) for x, n in counts.items()),
        key=lambda s: (-s.in_top, s.baseline_rank or 10**6, s.property_id),
    )[: max(k * 2, 5)]

    notes = [f"시나리오 {len(results)}개를 기준 조건과 비교했습니다. 유지율·tau 는 이 가정들에 대한 측정값이며 확률이 아닙니다."]
    if len(base.ranked) < k:
        notes.append(f"기준 조건의 후보가 {len(base.ranked)}개뿐이라 상위 {k}개 비교가 제한적입니다.")
    return SensitivityResult(
        top_k=k, baseline_top=base_top, baseline_candidates=len(base.ranked), scenarios=results,
        by_kind=by_kind, stable=stable, engine_calls=ctx.calls, notes=notes,
    )
