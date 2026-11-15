"""조건 추출 평가 (모델·프롬프트 비교용, 통합안 엔진 기준 — 2026-11-15 다시 만듦).

평가 세트: BE/eval/agent_cases.jsonl — 한 줄 = {id, message, context, expect}
- context: {current: 프로필, previous: 프로필|null, last_top: [...]|null}
- expect (있는 항목만 채점):
  intent: [허용 의도…]
  profile: {필드: 값}         — 서버 검증(apply_changes)까지 거친 '저장될 조건' (값 비교, 숫자는 ±0.5 허용)
  scenario: {필드: 값}        — what-if 로 계산할 가정 조건 (지금 조건은 그대로여야 함)
  unchanged: true             — 저장될 조건이 지금과 같아야 함
  includes: {필드: 값}         — 목록 필드에 값이 들어 있어야 함
  first_priority: 키          — 1순위 (travel / cost / area)
  timetable_len: n
  property_rank: n
  analysis: 종류              — intent=analyze 일 때 분석 종류
  places: [이름…]             — 저장될(또는 가정) 장소 조건 이름 (source_text, 순서 무관)
  place_rule: {이름: {tool, radius_m?, value?}} — 서버가 만든 확인 기준
  no_places: true             — 장소 조건이 생기면 안 됨 (확인할 수 없는 장소 등)
  unsupported: true           — 계산 불가 요청을 unsupported 로 표시

LLM 은 조건 추출(extract)만 부른다 (케이스 1개 = 호출 1회). 점수 계산·추천은 하지 않는다.
장소 조건은 실제 대화에서는 확인 질문('네') 뒤에 저장되지만, 여기서는 확인 전 계획(apply_changes 결과)을 채점한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent import tools
from app.agent.llm import AgentLLM, profile_context
from app.agent.schemas import AgentProfile, TurnExtraction
from app.agent.spec import capability_spec
from app.config import BE_DIR

CASES_PATH = BE_DIR / "eval" / "agent_cases.jsonl"
SAVING_INTENTS = ("recommend", "update", "analyze")  # 그래프의 update_profile 과 같음


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def context_for(case: dict[str, Any]) -> tuple[AgentProfile, AgentProfile | None, dict[str, Any]]:
    c = case.get("context") or {}
    cur = AgentProfile.model_validate(c.get("current") or {})
    prev = AgentProfile.model_validate(c["previous"]) if c.get("previous") else None
    ctx = profile_context(cur, prev, c.get("last_top"), [])
    ctx["service_spec"] = capability_spec(cur.university)
    return cur, prev, ctx


def resulting_profiles(ex: TurnExtraction, cur: AgentProfile, prev: AgentProfile | None
                       ) -> tuple[AgentProfile, AgentProfile | None]:
    """그래프의 update_profile 과 같은 규칙: (저장될 조건, 가정 조건)."""
    if ex.intent in SAVING_INTENTS:
        return tools.apply_changes(cur, ex.changes)[0], None
    if ex.intent == "what_if":
        return cur, tools.apply_changes(cur, ex.changes)[0]
    if ex.intent == "revert" and prev is not None:
        return prev, None
    return cur, None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= 0.5
    return a == b


def _get(p: AgentProfile, field: str) -> Any:
    return p.model_dump(mode="json")[field]


def score_case(case: dict[str, Any], ex: TurnExtraction) -> dict[str, bool]:
    cur, prev, _ = context_for(case)
    saved, scen = resulting_profiles(ex, cur, prev)
    e = case["expect"]
    checks: dict[str, bool] = {}
    if "intent" in e:
        checks["intent"] = ex.intent in e["intent"]
    for f, v in (e.get("profile") or {}).items():
        checks[f"profile.{f}"] = _same(_get(saved, f), v)
    for f, v in (e.get("scenario") or {}).items():
        checks[f"scenario.{f}"] = scen is not None and _same(_get(scen, f), v)
    if e.get("scenario"):
        checks["scenario.current_kept"] = saved == cur
    if e.get("unchanged"):
        checks["unchanged"] = saved == cur
    for f, v in (e.get("includes") or {}).items():
        checks[f"includes.{f}"] = v in _get(saved, f)
    if "first_priority" in e:
        checks["first_priority"] = bool(saved.priorities) and saved.priorities[0] == e["first_priority"]
    if "timetable_len" in e:
        checks["timetable_len"] = len(saved.timetable) == e["timetable_len"]
    if "property_rank" in e:
        checks["property_rank"] = ex.property_rank == e["property_rank"]
    if "analysis" in e:
        checks["analysis"] = ex.analysis == e["analysis"]
    target = scen if scen is not None else saved
    plans = {p.source_text: p for p in target.exclusions}
    if "places" in e:
        checks["places"] = sorted(plans) == sorted(e["places"])
    for name, rule in (e.get("place_rule") or {}).items():
        c = plans[name].conditions[0] if name in plans else None
        checks[f"place_rule.{name}"] = c is not None and c.tool == rule["tool"] \
            and ("radius_m" not in rule or c.radius_m == rule["radius_m"]) \
            and ("value" not in rule or c.exclude_if.value == rule["value"])
    if e.get("no_places"):
        checks["no_places"] = target.exclusions == cur.exclusions
    if e.get("unsupported"):
        checks["unsupported"] = bool(ex.unsupported)
    return checks


def run(llm: AgentLLM, cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for case in cases:
        _, _, ctx = context_for(case)
        try:
            ex = llm.extract(case["message"], ctx)
            checks = score_case(case, ex)
            rows.append({"id": case["id"], "message": case["message"], "ok": all(checks.values()), "checks": checks,
                         "extraction": ex.model_dump(mode="json", exclude_defaults=True)})
        except Exception as e:  # noqa: BLE001 — 평가는 끝까지 돌리고 실패로 센다
            rows.append({"id": case["id"], "message": case["message"], "ok": False, "checks": {},
                         "error": f"{type(e).__name__}: {getattr(e, 'kind', '')}"})
    n = len(rows)
    intent_rows = [r for r in rows if "intent" in r["checks"]]
    all_checks = [v for r in rows for v in r["checks"].values()]
    return {
        "cases": n,
        "case_accuracy": round(sum(r["ok"] for r in rows) / n, 3) if n else None,
        "intent_accuracy": round(sum(r["checks"]["intent"] for r in intent_rows) / len(intent_rows), 3) if intent_rows else None,
        "check_accuracy": round(sum(all_checks) / len(all_checks), 3) if all_checks else None,
        "errors": sum("error" in r for r in rows),
        "rows": rows,
    }
