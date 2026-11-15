"""조건 완화 분석 (지시서 6장).

후보가 없거나 적을 때, 실패한 조건(제외 사유)을 세고, 바꿀 수 있는 조건을 한 단계씩 완화한 가정 조건으로
엔진을 다시 돌려 후보가 얼마나 생기는지 보여 준다.

원칙
- 사용자가 잠근 조건(locked)은 바꾸지 않는다. 결과는 모두 가정(hypothetical)이며 조건을 저장하지 않는다.
- 예산 필터 정책(상한 + 10만 원 여유, 보증금 × 1.2)은 그대로 두고, 사용자의 상한 '값'만 가정으로 바꾼다.
- 학과 건물은 추천의 기본 입력이라 완화 대상에서 뺀다 (도보시간이 없는 건물 때문이면 안내만).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.decision.engine import EngineContext, summary
from app.scoring import RecommendRequest, RecommendResult

Lever = Literal["budget", "deposit", "timetable", "exclusions"]
LEVER_LABELS = {"budget": "유지비 상한", "deposit": "보증금 상한", "timetable": "시간표 건물", "exclusions": "챗봇 제외 조건"}

BUDGET_STEPS = (5, 10, 15, 20, 30)          # 만원
DEPOSIT_STEPS = (500, 1000, 2000)           # 만원 (마지막은 상한 없애기)


class FailureCounts(BaseModel):
    """기준 조건에서 단계별로 빠진 매물 수."""

    by_stage: dict[str, int]
    missing_walk_buildings: dict[str, int]  # 도보시간이 없어 빠진 매물 수 (건물별)
    by_exclusion_condition: dict[str, int]


class RelaxOption(BaseModel):
    lever: str
    label: str
    change: dict                 # 가정으로 바꾼 값 (예: {"budget_manwon": [70, 80]})
    magnitude: int               # 같은 lever 안에서 완화 단계 (1 = 가장 작은 변경)
    candidates: int
    added: int                   # 기준보다 늘어난 후보 수
    added_examples: list[dict]   # 새로 생긴 후보 중 엔진 순위가 높은 것
    top: list[dict]              # 이 가정에서의 상위 3
    reaches_target: bool
    hypothetical: bool = True


class RelaxationResult(BaseModel):
    baseline_candidates: int
    target_count: int
    needs_relaxation: bool
    failures: FailureCounts
    options: list[RelaxOption]          # lever 별·단계별 전체
    minimal: list[RelaxOption]          # lever 마다 목표 후보 수에 닿는 가장 작은 변경
    combined: RelaxOption | None        # 한 가지로 안 될 때 두 가지를 함께 완화한 가장 작은 조합
    locked: list[str]
    engine_calls: int
    notes: list[str]


def _failures(res: RecommendResult) -> FailureCounts:
    stage: dict[str, int] = {}
    walk: dict[str, int] = {}
    for e in res.excluded:
        stage[e.stage] = stage.get(e.stage, 0) + 1
        if e.stage == "missing" and (m := re.match(r"도보시간 정보 없음: (.+)", e.reason)):
            for b in m.group(1).split(", "):
                walk[b] = walk.get(b, 0) + 1
    return FailureCounts(by_stage=stage, missing_walk_buildings=walk, by_exclusion_condition=dict(res.exclusion_counts))


def _option(lever: str, label: str, change: dict, mag: int, base: RecommendResult, res: RecommendResult,
            target: int) -> RelaxOption:
    base_ids = {r.property_id for r in base.ranked}
    new = [r for r in res.ranked if r.property_id not in base_ids]
    return RelaxOption(
        lever=lever, label=label, change=change, magnitude=mag,
        candidates=len(res.ranked), added=len(res.ranked) - len(base.ranked),
        added_examples=[summary(r) for r in new[:3]],
        top=[summary(r) for r in res.ranked[:3]],
        reaches_target=len(res.ranked) >= target,
    )


class RelaxationRequest(BaseModel):
    request: RecommendRequest
    locked: list[Lever] = Field(default_factory=list, description="사용자가 바꾸지 않겠다고 한 조건")
    target_count: int = Field(3, ge=1, le=50, description="이 수만큼 후보가 생기면 충분하다고 본다")


def run_relaxation(body: RelaxationRequest, ctx: EngineContext | None = None) -> RelaxationResult:
    ctx = ctx or EngineContext()
    req, target, locked = body.request, body.target_count, set(body.locked)
    base = ctx.run(req)
    failures = _failures(base)
    options: list[RelaxOption] = []
    notes: list[str] = []

    if "budget" not in locked:
        for i, d in enumerate(BUDGET_STEPS, start=1):
            b = req.budget_manwon + d
            options.append(_option("budget", f"유지비 상한 +{d}만 원 ({req.budget_manwon:g} → {b:g})",
                                   {"budget_manwon": [req.budget_manwon, b]}, i, base,
                                   ctx.run(req.model_copy(update={"budget_manwon": b})), target))
    if "deposit" not in locked and req.deposit_budget_manwon is not None:
        for i, d in enumerate(DEPOSIT_STEPS, start=1):
            v = req.deposit_budget_manwon + d
            options.append(_option("deposit", f"보증금 상한 +{d:g}만 원 ({req.deposit_budget_manwon:g} → {v:g})",
                                   {"deposit_budget_manwon": [req.deposit_budget_manwon, v]}, i, base,
                                   ctx.run(req.model_copy(update={"deposit_budget_manwon": v})), target))
        options.append(_option("deposit", "보증금 상한 없애기", {"deposit_budget_manwon": [req.deposit_budget_manwon, None]},
                               len(DEPOSIT_STEPS) + 1, base,
                               ctx.run(req.model_copy(update={"deposit_budget_manwon": None})), target))
    if "timetable" not in locked and req.timetable:
        dept = set(req.resolved_department_buildings())
        tt_missing = [b for b, n in sorted(failures.missing_walk_buildings.items(), key=lambda kv: -kv[1]) if b not in dept]
        for i, b in enumerate(tt_missing, start=1):
            slots = [s for s in req.timetable if s.building != b]
            options.append(_option("timetable", f"시간표에서 '{b}' 수업 빼고 계산",
                                   {"timetable_without": b}, i, base,
                                   ctx.run(req.model_copy(update={"timetable": slots})), target))
        options.append(_option("timetable", "시간표 없이 (학과 건물만)", {"timetable": "none"}, len(tt_missing) + 1, base,
                               ctx.run(req.model_copy(update={"timetable": []})), target))
    if "exclusions" not in locked and req.exclusions:
        for i, plan in enumerate(req.exclusions, start=1):
            rest = [p for p in req.exclusions if p is not plan]
            options.append(_option("exclusions", f"챗봇 조건 빼기: {plan.source_text}", {"exclusion_removed": plan.source_text},
                                   i, base, ctx.run(req.model_copy(update={"exclusions": rest})), target))

    dept_missing = {b: n for b, n in failures.missing_walk_buildings.items() if b in set(req.resolved_department_buildings())}
    if dept_missing:
        notes.append("학과 건물까지 도보시간이 없는 매물이 빠졌습니다 (" +
                     ", ".join(f"{b} {n}개" for b, n in dept_missing.items()) + "). 학과 건물은 완화 대상이 아닙니다.")
    if locked:
        notes.append("바꾸지 않기로 한 조건: " + ", ".join(LEVER_LABELS[x] for x in sorted(locked)))

    minimal: list[RelaxOption] = []
    for lever in LEVER_LABELS:
        hit = [o for o in options if o.lever == lever and o.reaches_target and o.added > 0]
        if hit:
            minimal.append(min(hit, key=lambda o: o.magnitude))

    combined = None
    if not minimal and "budget" not in locked and "deposit" not in locked and req.deposit_budget_manwon is not None:
        # 한 가지로 안 되면 유지비·보증금을 함께 (단계 합이 작은 순)
        pairs = sorted(((i, j) for i in range(len(BUDGET_STEPS)) for j in range(len(DEPOSIT_STEPS))),
                       key=lambda ij: (ij[0] + ij[1], ij))
        for i, j in pairs:
            b, v = req.budget_manwon + BUDGET_STEPS[i], req.deposit_budget_manwon + DEPOSIT_STEPS[j]
            res = ctx.run(req.model_copy(update={"budget_manwon": b, "deposit_budget_manwon": v}))
            if len(res.ranked) >= target:
                combined = _option("budget+deposit", f"유지비 +{BUDGET_STEPS[i]}만 원 · 보증금 +{DEPOSIT_STEPS[j]:g}만 원",
                                   {"budget_manwon": [req.budget_manwon, b], "deposit_budget_manwon": [req.deposit_budget_manwon, v]},
                                   i + j + 2, base, res, target)
                break

    needs = len(base.ranked) < target
    if not needs:
        notes.append(f"지금 조건으로 후보가 {len(base.ranked)}개라 완화가 꼭 필요하지는 않습니다 (참고용 결과).")
    elif not minimal and combined is None:
        notes.append("검토한 완화 범위 안에서는 목표 후보 수에 닿지 않았습니다.")
    notes.append("모든 결과는 가정이며 생활 프로필에 저장되지 않습니다.")
    return RelaxationResult(
        baseline_candidates=len(base.ranked), target_count=target, needs_relaxation=needs, failures=failures,
        options=options, minimal=minimal, combined=combined, locked=sorted(locked), engine_calls=ctx.calls, notes=notes,
    )
