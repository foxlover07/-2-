"""평가 실행기 (rules-eval-plan.md 4·5장). 실제 LLM 을 부른다 — 실행은 scripts/eval_agent.py.

- L2 조건 추출: 케이스 1개 = 추출 1회. 서버 규칙(tools.apply_changes)까지 거친 '저장될 조건'을 비교한다.
- L3 제외 계획: 케이스 1개 = 계획 1회. 서버 검증(plan_from_draft·merge_plans)을 거친 계획을 match_plan 으로 판정한다.
- L4 대화: 그래프를 실제 데이터로 턴마다 돌린다 (추출 + 계획 + 설명). 상태와 답 문장을 판정한다.

결과는 한 줄씩 바로 jsonl 에 쓴다 (한도 오류로 멈춰도 이어서 돌릴 수 있게). 매물 주소·답 원문이 들어가므로 커밋하지 않는다.
요약 표(md)에는 통과 수·실패한 케이스 id·실패한 검사 이름만 쓴다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from app.agent import tools
from app.agent.graph import build_graph, numbers_grounded, profile_context, run_turn
from app.agent.llm import AgentLLM, LLMError
from app.agent.schemas import AgentProfile, TurnExtraction
from app.exclusions import ExclusionPlan, tool_catalog
from eval.cases_check import PROFILE_FIELDS, load, match_plan

LAYERS = {"L2": "l2_extract.jsonl", "L3": "l3_plan.jsonl", "L4": "l4_dialog.jsonl"}


class QuotaStop(RuntimeError):
    """하루 한도에 걸려 멈춤 (이어 하기)."""


class Paced:
    """LLM 호출 사이 간격을 둔다 (분당 rpm 회 이하). 한도 오류(quota)는 QuotaStop 으로 올린다."""

    def __init__(self, llm: AgentLLM, rpm: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.llm, self.gap, self._sleep, self._clock = llm, 60.0 / rpm if rpm > 0 else 0.0, sleep, clock
        self._last = -1e9
        self.calls = 0

    @property
    def last_model(self) -> str | None:
        return getattr(self.llm, "last_model", None)

    def _go(self, name: str, *args: Any) -> Any:
        wait = self._last + self.gap - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()
        self.calls += 1
        try:
            return getattr(self.llm, name)(*args)
        except LLMError as e:
            if e.kind == "quota":
                raise QuotaStop(str(e)) from e
            raise

    def extract(self, *a: Any) -> Any:
        return self._go("extract", *a)

    def plan(self, *a: Any) -> Any:
        return self._go("plan", *a)

    def explain(self, *a: Any) -> Any:
        return self._go("explain", *a)


# ---------------------------------------------------------------- 공통

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
    if want is None:  # 케이스의 null = 비어 있음 (목록 칸은 [])
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


# ---------------------------------------------------------------- L2

def resolve_turn(ex: TurnExtraction, cur: AgentProfile, prev: AgentProfile | None
                 ) -> tuple[str, AgentProfile, AgentProfile | None, list[str], list[str]]:
    """그래프 update_profile 과 같은 규칙 → (의도, 저장될 조건, 가정 조건, 다시 물을 칸, 안내)."""
    intent = ex.intent
    if ex.changes.university and tools.normalize_university(ex.changes.university) is None \
            and intent not in ("out_of_scope", "greeting"):
        return "out_of_scope", cur, None, [], []
    if intent in ("update", "recommend", "exclude"):
        saved, notes, asks = tools.apply_changes(cur, ex.changes)
        return intent, saved, None, asks, notes
    if intent == "what_if":
        scen, notes, asks = tools.apply_changes(cur, ex.changes)
        return intent, cur, scen, asks, notes
    if intent == "revert" and prev is not None:
        return intent, prev, None, [], []
    if intent == "remove_exclusion":
        kept, _ = tools.remove_exclusions(cur.exclusions, ex.remove_exclusion)
        return intent, cur.model_copy(update={"exclusions": kept}), None, [], []
    return intent, cur, None, [], []


def check_l2(case: dict[str, Any], ex: TurnExtraction) -> dict[str, bool]:
    c = case["context"]
    cur = _profile_of(c.get("current"), c.get("exclusions") or [])
    prev = _profile_of(c["previous"]) if c.get("previous") else None
    intent, saved, scen, asks, notes = resolve_turn(ex, cur, prev)
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
        pending = set(asks) | set(tools.missing_fields(saved))
        pending |= {k.split(".")[0] for k in pending if k.startswith("priorities")}
        out["missing_includes"] = all(k in pending for k in e["missing_includes"])
    for f in e.get("not_set", []):
        out[f"not_set.{f}"] = f not in AgentProfile.model_fields or getattr(saved, f) in (None, [])
    if "has_exclusion_request" in e:
        out["has_exclusion_request"] = bool(ex.exclusion_request or intent == "exclude") == e["has_exclusion_request"]
    if e.get("unsupported_nonempty"):
        out["unsupported_nonempty"] = bool(ex.unsupported)
    if "property_rank" in e:
        out["property_rank"] = ex.property_rank == e["property_rank"]
    return out


def run_l2(llm: AgentLLM, case: dict[str, Any]) -> dict[str, Any]:
    c = case["context"]
    cur = _profile_of(c.get("current"), c.get("exclusions") or [])
    prev = _profile_of(c["previous"]) if c.get("previous") else None
    ctx = profile_context(cur, prev, None, [], c.get("last_question") or [])
    ex = llm.extract(case["message"], ctx)
    checks = check_l2(case, ex)
    return {"checks": checks, "output": ex.model_dump(mode="json", exclude_defaults=True)}


# ---------------------------------------------------------------- L3

def run_l3(llm: AgentLLM, case: dict[str, Any], props: list[Any]) -> dict[str, Any]:
    c = case["context"]
    uni_props = [p for p in props if p.university == c.get("university")] or props
    existing = [ExclusionPlan.model_validate(x) for x in c.get("exclusions") or []]
    ctx = {"catalog": tool_catalog(uni_props or None), "exclusions": [x.describe() for x in tools.all_conditions(existing)]}
    draft = llm.plan(case["message"], ctx)
    plan, _notes = tools.plan_from_draft(draft, case["message"], uni_props or None)
    if plan.conditions:
        merged, _ = tools.merge_plans(existing, plan)
        final = merged[-1].model_copy(update={"unsupported": plan.unsupported, "onsite_checks": plan.onsite_checks})
    else:
        final = plan
    why = match_plan(final.model_dump(mode="json"), case["expect"])
    return {"checks": {"plan": not why}, "why": why, "output": final.model_dump(mode="json")}


# ---------------------------------------------------------------- L4

def _asked(state: dict[str, Any]) -> set[str]:
    asks = set(state.get("asks") or [])
    return asks | {k.split(".")[0] for k in asks}


def check_l4_turn(e: dict[str, Any], state: dict[str, Any], before: AgentProfile) -> dict[str, bool]:
    text = state.get("response", "")
    after = _profile_of(state.get("current_profile"))
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
        out["numbers_grounded"] = numbers_grounded(text, state.get("facts") or {})
    if "max_questions" in e:
        out["max_questions"] = len(state.get("asks") or []) <= e["max_questions"]
    if "asks_for_any" in e:
        out["asks_for_any"] = bool(_asked(state) & set(e["asks_for_any"]))
    if "asks_for_all" in e:
        out["asks_for_all"] = set(e["asks_for_all"]) <= _asked(state)
    if "recommended" in e:
        out["recommended"] = bool(state.get("recommended")) == e["recommended"]
    if "exclusions_count" in e:
        out["exclusions_count"] = len(tools.all_conditions(after.exclusions)) == e["exclusions_count"]
    if "timetable_len" in e:
        out["timetable_len"] = len(after.timetable) == e["timetable_len"]
    if "when_zero_left" in e or "when_some_left" in e:
        if state.get("exclusion_blocked"):
            w = e.get("when_zero_left") or {}
            if "applied" in w:
                out["zero.applied"] = bool(state.get("exclusion_applied")) == w["applied"]
            if "exclusions_count" in w:
                out["zero.exclusions_count"] = len(tools.all_conditions(after.exclusions)) == w["exclusions_count"]
            if w.get("asks"):
                out["zero.asks"] = "?" in text
        else:
            w = e.get("when_some_left") or {}
            if "starts_with" in w:
                out["some.starts_with"] = text.startswith(w["starts_with"])
            if "exclusions_count" in w:
                out["some.exclusions_count"] = len(tools.all_conditions(after.exclusions)) == w["exclusions_count"]
    if "when_over_budget_in_top" in e:
        rec = state.get("scenario_recommendation") or state.get("last_recommendation") or {}
        if any(t.get("over_budget") for t in rec.get("top", [])):
            out["over_budget.shown"] = all(w in text for w in e["when_over_budget_in_top"].get("contains_all", []))
    return out


def run_l4(llm: AgentLLM, case: dict[str, Any], data: Callable[[], Any]) -> dict[str, Any]:
    g = build_graph(lambda: llm, data)
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
        outputs.append({"intent": state.get("intent"), "status": state.get("status"), "asks": state.get("asks"),
                        "response": state.get("response"), "source": (state.get("trace") or {}).get("explanation_source")})
        before = _profile_of(state.get("current_profile"))
    return {"checks": checks, "output": outputs}


# ---------------------------------------------------------------- 실행

def select(layer: str, split: str = "all", ids: Iterable[str] = ()) -> list[dict[str, Any]]:
    rows = load(LAYERS[layer])
    want = set(ids)
    return [r for r in rows if (split == "all" or r["split"] == split) and (not want or r["id"] in want)]


def run(llm: AgentLLM, layers: Iterable[str], out_path: Path, *, split: str = "all", ids: Iterable[str] = (),
        data: Callable[[], Any], done: set[tuple[str, str]] = frozenset(), log: Callable[[str], None] = print) -> bool:
    """케이스를 돌리며 한 줄씩 out_path 에 쓴다. 한도에 걸리면 False (이어 하기)."""
    props = list(data()[0])
    with out_path.open("a", encoding="utf-8") as f:
        for layer in layers:
            for case in select(layer, split, ids):
                if (layer, case["id"]) in done:
                    continue
                t0 = time.monotonic()
                try:
                    if layer == "L2":
                        res = run_l2(llm, case)
                    elif layer == "L3":
                        res = run_l3(llm, case, props)
                    else:
                        res = run_l4(llm, case, data)
                    err = None
                except QuotaStop:
                    log(f"한도에 걸려 멈춤: {layer} {case['id']} 부터 이어서 돌리면 됩니다 (--resume).")
                    return False
                except LLMError as e:
                    res, err = {"checks": {}, "output": None}, f"LLMError:{e.kind}"
                except Exception as e:  # noqa: BLE001 — 평가는 끝까지 돌리고 실패로 센다
                    res, err = {"checks": {}, "output": None}, f"{type(e).__name__}: {str(e)[:200]}"
                ok = bool(res["checks"]) and all(res["checks"].values()) and err is None
                row = {"layer": layer, "id": case["id"], "split": case["split"], "rules": case["rules"],
                       "safety": case.get("safety", False), "ok": ok, "checks": res["checks"], "why": res.get("why"),
                       "error": err, "s": round(time.monotonic() - t0, 1), "output": res.get("output")}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                log(f"{'✓' if ok else '✗'} {layer} {case['id']} ({row['s']}s)"
                    + ("" if ok else " — " + (err or ", ".join(k for k, v in res["checks"].items() if not v))))
    return True


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        latest[(r["layer"], r["id"])] = r  # 같은 케이스를 다시 돌렸으면 마지막 것
    return list(latest.values())


def tally(rows: list[dict[str, Any]]) -> dict[str, dict[str, tuple[int, int]]]:
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for layer in LAYERS:
        rs = [r for r in rows if r["layer"] == layer]
        groups = {"전체": rs, "개발용": [r for r in rs if r["split"] == "dev"],
                  "평가용": [r for r in rs if r["split"] == "heldout"]}
        if layer == "L4":
            groups["안전 규칙"] = [r for r in rs if r["safety"]]
            groups["그 밖"] = [r for r in rs if not r["safety"]]
        out[layer] = {k: (sum(r["ok"] for r in v), len(v)) for k, v in groups.items()}
    return out


PASS = {"L2": 0.90, "L3": 0.85, "L4.안전 규칙": 1.0, "L4.그 밖": 0.85}  # rules-eval-plan.md 4-6 (제안)


def _pct(ok: int, n: int) -> str:
    return f"{ok}/{n} ({ok / n:.0%})" if n else "-"


def summary_md(rows: list[dict[str, Any]], meta: dict[str, Any], before: list[dict[str, Any]] | None = None) -> str:
    """요약 표. 매물 주소·답 원문은 넣지 않는다 (공개 저장소)."""
    t = tally(rows)
    lines = [f"# 챗봇 평가 결과 — {meta.get('label', '')}", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items() if k != "label"]
    lines += ["", "| 층 | 묶음 | 통과 | 기준 |", "|---|---|---|---|"]
    for layer, groups in t.items():
        for g, (ok, n) in groups.items():
            if not n:
                continue
            crit = PASS.get(f"{layer}.{g}") or (PASS.get(layer) if g == "전체" else None)
            mark = "" if crit is None else f"{crit:.0%} {'✅' if ok / n >= crit else '❌'}"
            lines.append(f"| {layer} | {g} | {_pct(ok, n)} | {mark} |")
    if before is not None:
        tb = tally(before)
        lines += ["", "## 1차 → 2차", "", "| 층 | 묶음 | 전 | 후 |", "|---|---|---|---|"]
        for layer in t:
            for g in t[layer]:
                a, b = tb.get(layer, {}).get(g, (0, 0)), t[layer][g]
                if a[1] or b[1]:
                    lines.append(f"| {layer} | {g} | {_pct(*a)} | {_pct(*b)} |")
    fails = [r for r in rows if not r["ok"]]
    by_rule: dict[str, int] = {}
    for r in fails:
        for rid in r["rules"]:
            by_rule[rid] = by_rule.get(rid, 0) + 1
    if by_rule:
        lines += ["", "## 실패가 많은 규칙", ""]
        lines += [f"- {k}: {v}개" for k, v in sorted(by_rule.items(), key=lambda kv: -kv[1])[:8]]
    if fails:
        lines += ["", "## 실패한 케이스 (원인은 직접 분류: 프롬프트 / 규칙 문서 / 코드 / 기대값 / 데이터)", "",
                  "| 층 | id | 묶음 | 실패한 검사 | 원인 |", "|---|---|---|---|---|"]
        for r in sorted(fails, key=lambda r: (r["layer"], r["id"])):
            bad = r["error"] or ", ".join(k for k, v in r["checks"].items() if not v)
            lines.append(f"| {r['layer']} | {r['id']} | {'평가용' if r['split'] == 'heldout' else '개발용'} | {bad} | |")
    return "\n".join(lines) + "\n"
