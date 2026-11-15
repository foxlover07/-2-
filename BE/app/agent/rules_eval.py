"""규칙 기반 3단계 평가 (agent-integrated 의 eval/runner.py · cases_check.py 를 이 브랜치 그래프에 맞게 옮김, 2026-11-16).

케이스: BE/eval/cases/{l2_extract,l3_plan,l4_dialog}.jsonl — 의도 이름은 이 브랜치 것으로 바꿔 두었다
(compare→compare_previous, greeting→smalltalk, exclude·remove_exclusion→update). 케이스마다 규칙 번호(rules)·split(dev/heldout).
- L2 조건 추출: 말 → 의도·저장될 조건 (LLM extract 1회)
- L3 제외 계획: 기호 문장 → 확인 계획 (LLM plan 1회). 장소 기호도 계획 단계로 직접 시험한다
- L4 대화: 여러 턴을 실제 그래프로 돌려 답 문장까지 본다 (턴마다 추출 + 설명, 기호 턴은 계획까지)
평가용(heldout)은 프롬프트를 고칠 때 보지 않는다. 실제 LLM 실행은 scripts/eval_rules.py (사용자 승인 후).
"""

from __future__ import annotations

import json
import operator
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.agent import tools
from app.agent.graph import build_facts, build_graph, numbers_grounded, run_turn, to_response_parts
from app.agent.llm import AgentLLM, profile_context
from app.agent.schemas import AgentProfile, TurnExtraction
from app.agent.spec import capability_spec
from app.config import BE_DIR
from app.data import load_properties
from app.exclusions import ExclusionPlan, tool_catalog

CASES = BE_DIR / "eval" / "cases"
LAYERS = {"L2": "l2_extract.jsonl", "L3": "l3_plan.jsonl", "L4": "l4_dialog.jsonl"}
PROFILE_FIELDS = ("university", "department_buildings", "priorities", "budget_manwon", "deposit_budget_manwon",
                  "desired_commute_min", "desired_area_m2", "larger_area", "timetable")
# tools.missing_fields 의 안내 문구 → 칸 이름 (케이스의 asks·missing_includes 는 칸 이름)
MISSING_KEYS = {"학교": "university", "월 유지비(월세+관리비) 상한": "budget_manwon",
                "우선순위 1·2순위 (이동 시간·예산·면적)": "priorities", "희망 통학 시간 (편도, 분)": "desired_commute_min",
                "희망 면적 (㎡)": "desired_area_m2", "수업을 주로 듣는 학과 건물": "department_buildings"}
_NUM_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


def load(layer: str) -> list[dict[str, Any]]:
    path = CASES / LAYERS[layer]
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _profile_of(ctx: dict[str, Any] | None, exclusions: Iterable[dict[str, Any]] = ()) -> AgentProfile:
    d = dict(ctx or {})
    if exclusions:
        d["exclusions"] = list(exclusions)
    return AgentProfile.model_validate(d)


def _get(p: AgentProfile, path: str) -> Any:
    d = p.model_dump(mode="json")
    key, _, idx = path.partition(".")
    v = d.get(key)
    if idx and isinstance(v, list):
        i = int(idx)
        return v[i] if i < len(v) else None
    return v


def _same(actual: Any, want: Any) -> bool:
    if want is None:
        return actual in (None, [])
    if isinstance(want, dict) and "approx" in want:
        return isinstance(actual, (int, float)) and abs(actual - want["approx"]) <= want.get("tol", 0)
    if isinstance(want, (int, float)) and isinstance(actual, (int, float)):
        return abs(actual - want) < 1e-9
    return actual == want


def _fields_equal(a: AgentProfile, b: AgentProfile, skip: Iterable[str] = ()) -> bool:
    da, db = a.model_dump(mode="json"), b.model_dump(mode="json")
    skip_base = {s.split(".")[0] for s in skip}
    return all(da[k] == db[k] for k in PROFILE_FIELDS if k not in skip_base)


def _missing_keys(p: AgentProfile) -> set[str]:
    return {MISSING_KEYS.get(m, m) for m in tools.missing_fields(p)}


# ---------------------------------------------------------------- L2

def resolve_turn(ex: TurnExtraction, cur: AgentProfile, prev: AgentProfile | None
                 ) -> tuple[str, AgentProfile, AgentProfile | None]:
    """그래프 extract·update_profile 과 같은 규칙 → (의도, 저장될 조건, 가정 조건). 계획 단계(LLM)는 L3 에서 따로 본다."""
    intent = ex.intent
    if ex.exclusion_request and intent in ("smalltalk", "explain"):
        intent = "update"
    if intent in ("recommend", "update", "analyze"):
        return intent, tools.apply_changes(cur, ex.changes)[0], None
    if intent == "what_if":
        return intent, cur, tools.apply_changes(cur, ex.changes)[0]
    if intent == "revert" and prev is not None:
        return intent, prev, None
    return intent, cur, None


def check_l2(case: dict[str, Any], ex: TurnExtraction) -> dict[str, bool]:
    c = case["context"]
    cur = _profile_of(c.get("current"), c.get("exclusions") or [])
    prev = _profile_of(c["previous"]) if c.get("previous") else None
    intent, saved, scen = resolve_turn(ex, cur, prev)
    e = case["expect"]
    out: dict[str, bool] = {}
    if "intent" in e:
        out["intent"] = intent in e["intent"]
    for f, v in (e.get("profile") or {}).items():
        out[f"profile.{f}"] = _same(_get(saved, f), v)
    if e.get("others_unchanged"):
        out["others_unchanged"] = _fields_equal(saved, cur, skip=(e.get("profile") or {}).keys())
    if e.get("unchanged"):
        out["unchanged"] = _fields_equal(saved, cur)
    for f, v in (e.get("scenario") or {}).items():
        out[f"scenario.{f}"] = scen is not None and _same(_get(scen, f), v)
    for f, v in (e.get("includes") or {}).items():
        out[f"includes.{f}"] = v in (_get(saved, f) or [])
    if "timetable" in e:
        out["timetable"] = _get(saved, "timetable") == e["timetable"]
    if "timetable_len" in e:
        out["timetable_len"] = len(saved.timetable) == e["timetable_len"]
    if "missing_includes" in e:
        out["missing_includes"] = set(e["missing_includes"]) <= _missing_keys(saved)
    for f in e.get("not_set", []):
        out[f"not_set.{f}"] = f not in AgentProfile.model_fields or getattr(saved, f) in (None, [])
    if "has_exclusion_request" in e:  # 이 브랜치: 장소 기호(place_prefs)도 기호로 센다
        has = bool(ex.exclusion_request or ex.changes.place_prefs or ex.changes.remove_exclusions)
        out["has_exclusion_request"] = has == e["has_exclusion_request"]
    if e.get("unsupported_nonempty"):
        out["unsupported_nonempty"] = bool(ex.unsupported)
    if "property_rank" in e:
        out["property_rank"] = ex.property_rank == e["property_rank"]
    return out


def run_l2(llm: AgentLLM, case: dict[str, Any]) -> dict[str, Any]:
    c = case["context"]
    cur = _profile_of(c.get("current"), c.get("exclusions") or [])
    prev = _profile_of(c["previous"]) if c.get("previous") else None
    ctx = profile_context(cur, prev, None, [])
    ctx["service_spec"] = capability_spec(cur.university)
    ex = llm.extract(case["message"], ctx)
    return {"checks": check_l2(case, ex), "output": ex.model_dump(mode="json", exclude_defaults=True)}


# ---------------------------------------------------------------- L3

def _excluded(cond: dict[str, Any], probe: Any) -> bool:
    ei = cond.get("exclude_if") or {}
    op, value = ei.get("op"), ei.get("value")
    if op in ("in", "not_in"):
        values = value if isinstance(value, list) else [value]
        return (probe in values) if op == "in" else (probe not in values)
    fn = _NUM_OPS.get(op)
    return bool(fn and isinstance(value, (int, float)) and fn(probe, value))


def match_condition(cond: dict[str, Any], m: dict[str, Any]) -> list[str]:
    if "any_of" in m:
        tried = [match_condition(cond, alt) for alt in m["any_of"]]
        return [] if any(not r for r in tried) else ["any_of: " + " / ".join("; ".join(r) for r in tried)]
    why: list[str] = []
    want_tools = m["tool"] if isinstance(m["tool"], list) else [m["tool"]]
    if cond.get("tool") not in want_tools:
        return [f"tool {cond.get('tool')} ≠ {want_tools}"]
    for key in ("field", "amenity", "metric"):
        if key in m and cond.get(key) != m[key]:
            why.append(f"{key} {cond.get(key)} ≠ {m[key]}")
    if "keywords_any" in m:
        words = " ".join(cond.get("keywords") or []) + " " + (cond.get("label") or "")
        if not any(k in words for k in m["keywords_any"]):
            why.append(f"keywords {cond.get('keywords')} 에 {m['keywords_any']} 없음")
    for p in m.get("excludes", []):
        if not _excluded(cond, p):
            why.append(f"{p} 이 빠져야 함")
    for p in m.get("keeps", []):
        if _excluded(cond, p):
            why.append(f"{p} 이 남아야 함")
    return why


def match_plan(plan: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    conds = list(plan.get("conditions") or [])
    why: list[str] = []
    if expect.get("no_conditions") and conds:
        why.append(f"조건이 없어야 하는데 {len(conds)}개")
    used: set[int] = set()
    for m in expect.get("conditions", []):
        hit = next((i for i, c in enumerate(conds) if i not in used and not match_condition(c, m)), None)
        if hit is None:
            detail = [match_condition(c, m) for c in conds] or [["조건 없음"]]
            why.append(f"기대 조건 없음 {m}: " + " | ".join("; ".join(d) for d in detail))
        else:
            used.add(hit)
    if expect.get("exact") and len(conds) > len(used):
        why.append(f"기대하지 않은 조건 {len(conds) - len(used)}개")
    missing_onsite = set(expect.get("onsite", [])) - set(plan.get("onsite_checks") or [])
    if missing_onsite:
        why.append(f"현장 확인 빠짐: {sorted(missing_onsite)}")
    if expect.get("unsupported_nonempty") and not plan.get("unsupported"):
        why.append("unsupported 가 비어 있음")
    return why


def run_l3(llm: AgentLLM, case: dict[str, Any]) -> dict[str, Any]:
    c = case["context"]
    props = list(load_properties())
    uni_props = [p for p in props if p.university == c.get("university")] or props
    existing = [ExclusionPlan.model_validate(x) for x in c.get("exclusions") or []]
    ctx = {"catalog": tool_catalog(uni_props or None),
           "exclusions": [cd.describe() for e in existing for cd in e.conditions]}
    draft = llm.plan(case["message"], ctx)
    plan, _ = tools.plan_from_draft(draft, case["message"], uni_props or None)
    if plan.conditions:
        merged, _ = tools.merge_plans(existing, plan)
        final = merged[-1].model_copy(update={"unsupported": plan.unsupported, "onsite_checks": plan.onsite_checks})
    else:
        final = plan
    why = match_plan(final.model_dump(mode="json"), case["expect"])
    return {"checks": {"plan": not why}, "why": why, "output": final.model_dump(mode="json")}


# ---------------------------------------------------------------- L4

def check_l4_turn(e: dict[str, Any], state: dict[str, Any], before: AgentProfile) -> dict[str, bool]:
    text = state.get("response", "")
    after = _profile_of(state.get("current_profile"))
    asks = {MISSING_KEYS.get(m, m) for m in state.get("pending_required_fields") or []}
    recommended = to_response_parts(state).get("recommendation_results") is not None
    n_after = sum(len(x.conditions) for x in after.exclusions)
    blocked = "아직 적용하지 않았어요" in (state.get("lead_text") or "")
    out: dict[str, bool] = {}
    if "intent" in e:
        out["intent"] = state.get("intent") in e["intent"]
    for f, v in (e.get("profile") or {}).items():
        out[f"profile.{f}"] = _same(_get(after, f), v)
    if e.get("unchanged"):
        out["unchanged"] = _fields_equal(after, before) and after.exclusions == before.exclusions
    if "starts_with" in e:
        out["starts_with"] = text.startswith(e["starts_with"])
    if "contains_all" in e:
        out["contains_all"] = all(w in text for w in e["contains_all"])
    if "contains_any" in e:
        out["contains_any"] = any(w in text for w in e["contains_any"])
    if "not_contains" in e:
        out["not_contains"] = not any(w in text for w in e["not_contains"])
    if e.get("numbers_grounded"):
        out["numbers_grounded"] = numbers_grounded(text, build_facts(state))
    if "max_questions" in e:
        out["max_questions"] = len(asks) <= e["max_questions"] or bool(state.get("confirm_question"))
    if "asks_for_any" in e:
        out["asks_for_any"] = bool(asks & set(e["asks_for_any"]))
    if "asks_for_all" in e:
        out["asks_for_all"] = set(e["asks_for_all"]) <= asks
    if "recommended" in e:
        out["recommended"] = recommended == e["recommended"]
    if "exclusions_count" in e:
        out["exclusions_count"] = n_after == e["exclusions_count"]
    if "timetable_len" in e:
        out["timetable_len"] = len(after.timetable) == e["timetable_len"]
    if "when_zero_left" in e or "when_some_left" in e:
        w = (e.get("when_zero_left") if blocked else e.get("when_some_left")) or {}
        tag = "zero" if blocked else "some"
        if "applied" in w:
            out[f"{tag}.applied"] = (n_after > sum(len(x.conditions) for x in before.exclusions)) == w["applied"]
        if "exclusions_count" in w:
            out[f"{tag}.exclusions_count"] = n_after == w["exclusions_count"]
        if w.get("asks"):
            out[f"{tag}.asks"] = "?" in text
        if "starts_with" in w:
            out[f"{tag}.starts_with"] = text.startswith(w["starts_with"])
    if "when_over_budget_in_top" in e:
        rec = state.get("scenario_recommendation") or state.get("last_recommendation") or {}
        if any(t.get("over_budget") for t in rec.get("top", [])):
            out["over_budget.shown"] = all(w in text for w in e["when_over_budget_in_top"].get("contains_all", []))
    return out


def run_l4(llm: AgentLLM, case: dict[str, Any]) -> dict[str, Any]:
    g = build_graph(lambda: llm)
    c = case["context"]
    start = _profile_of(c.get("current"), c.get("exclusions") or [])
    fe = start.model_dump(mode="json") if (c.get("current") or c.get("exclusions")) else None
    before = start
    checks: dict[str, bool] = {}
    outputs = []
    for i, t in enumerate(case["turns"], 1):
        state = run_turn(g, f"eval-{case['id']}", t["user"], fe if i == 1 else None)
        for k, v in check_l4_turn(t["expect"], state, before).items():
            checks[f"t{i}.{k}"] = v
        outputs.append({"intent": state.get("intent"), "status": state.get("status"),
                        "response": state.get("response"), "source": (state.get("trace") or {}).get("explanation_source")})
        before = _profile_of(state.get("current_profile"))
    return {"checks": checks, "output": outputs}


# ---------------------------------------------------------------- 실행

def select(layer: str, split: str = "all", ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    rows = load(layer)
    want = set(ids)
    return [r for r in rows if (split == "all" or r.get("split") == split) and (not want or r["id"] in want)]


def run_case(llm: AgentLLM, layer: str, case: dict[str, Any]) -> dict[str, Any]:
    try:
        res = {"L2": run_l2, "L3": run_l3, "L4": run_l4}[layer](llm, case)
        ok = bool(res["checks"]) and all(res["checks"].values())
    except Exception as e:  # noqa: BLE001 — 평가는 끝까지 돌리고 실패로 센다
        res, ok = {"checks": {}, "error": f"{type(e).__name__}: {getattr(e, 'kind', '') or e}"}, False
    return {"layer": layer, "id": case["id"], "split": case.get("split"), "rules": case.get("rules", []),
            "safety": case.get("safety", False), "ok": ok, **res}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for layer in LAYERS:
        rs = [r for r in rows if r["layer"] == layer]
        if not rs:
            continue
        parts = {"전체": rs, "개발용": [r for r in rs if r["split"] == "dev"],
                 "평가용": [r for r in rs if r["split"] == "heldout"]}
        if layer == "L4":
            parts["안전 규칙"] = [r for r in rs if r["safety"]]
        out[layer] = {k: f"{sum(r['ok'] for r in v)}/{len(v)}" for k, v in parts.items() if v}
    return out


def save(rows: list[dict[str, Any]], path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**meta, "summary": summarize(rows), "rows": rows}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
