"""평가 케이스 형식 검사와 L3 판정 (rules-eval-plan.md 4장).

- check_all(): 케이스 파일이 지금 코드·규칙 문서와 맞는지 검사한다 (LLM 을 부르지 않는다).
  · 규칙 번호가 docs/chatbot-rules.md 에 있는가
  · 학교·건물·우선순위·의도 값이 고를 수 있는 값인가
  · L3 의 기대 조건이 확인 도구에 있는 칸·시설·값인가, 정답 예시(reference)가 ExclusionPlan 으로 검증되고 기대를 통과하는가
- match_plan(): LLM 이 만든 확인 계획을 L3 기대와 비교한다. 실행기가 그대로 쓴다.

L3 기대는 기준값 하나가 아니라 '빠져야 하는 값(excludes)'과 '남아야 하는 값(keeps)'으로 적는다.
"30m 이하 제외"와 "31m 미만 제외"처럼 같은 뜻의 다른 표현을 모두 통과시키고,
기준이 모호한 말("가까웠으면")은 허용 범위를 둘 수 있다.
"""

from __future__ import annotations

import json
import operator
import re
from pathlib import Path
from typing import Any

from app.exclusions import AMENITIES, BUILDING_FIELDS, LISTING_FIELDS, ONSITE_CHECKS, ExclusionPlan
from app.scoring import FACTORS

BE = Path(__file__).resolve().parents[1]
CASES = BE / "eval" / "cases"
RULES_DOC = BE.parent / "docs" / "chatbot-rules.md"

INTENTS = ("update", "recommend", "what_if", "revert", "compare", "explain", "exclude", "remove_exclusion", "out_of_scope")
SPLITS = ("dev", "heldout")
PROFILE_FIELDS = ("university", "department_buildings", "priorities", "budget_manwon", "deposit_budget_manwon",
                  "desired_commute_min", "desired_area_m2", "larger_area", "timetable")
DAYS = ("월", "화", "수", "목", "금", "토", "일")
L2_KEYS = {"intent", "profile", "scenario", "unchanged", "others_unchanged", "includes", "missing_includes", "not_set",
           "timetable", "timetable_len", "has_exclusion_request", "unsupported_nonempty", "property_rank"}
L3_KEYS = {"conditions", "exact", "no_conditions", "onsite", "unsupported_nonempty"}
L4_KEYS = {"intent", "profile", "unchanged", "starts_with", "contains_all", "contains_any", "not_contains", "numbers_grounded",
           "max_questions", "asks_for_any", "asks_for_all", "recommended", "exclusions_count", "timetable_len",
           "when_zero_left", "when_some_left", "when_over_budget_in_top"}
_NUM_OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne}


def load(name: str) -> list[dict[str, Any]]:
    with open(CASES / name, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def rule_ids() -> set[str]:
    return set(re.findall(r"\*\*(R-\d{3,4}) ", RULES_DOC.read_text(encoding="utf-8")))


def buildings_by_university() -> dict[str, set[str]]:
    """규칙 문서 R-101 표에서 대학별 고를 수 있는 건물을 읽는다."""
    out: dict[str, set[str]] = {}
    for line in RULES_DOC.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (\S+대학교) \| (.+) \|$", line)
        if m:
            out[m.group(1)] = {b.strip() for b in m.group(2).split(",")}
    return out


# ---------------------------------------------------------------- L3 판정

def _excluded(cond: dict[str, Any], probe: Any) -> bool:
    """조건이 probe 값을 가진 매물을 빼는가."""
    ei = cond.get("exclude_if") or {}
    op, value = ei.get("op"), ei.get("value")
    if op in ("in", "not_in"):
        values = value if isinstance(value, list) else [value]
        return (probe in values) if op == "in" else (probe not in values)
    fn = _NUM_OPS.get(op)
    return bool(fn and isinstance(value, (int, float)) and fn(probe, value))


def match_condition(cond: dict[str, Any], m: dict[str, Any]) -> list[str]:
    """조건 하나가 기대(m)에 맞으면 빈 목록, 아니면 이유 목록."""
    if "any_of" in m:
        tried = [match_condition(cond, alt) for alt in m["any_of"]]
        return [] if any(not r for r in tried) else ["any_of: " + " / ".join("; ".join(r) for r in tried)]
    why: list[str] = []
    tools = m["tool"] if isinstance(m["tool"], list) else [m["tool"]]
    if cond.get("tool") not in tools:
        return [f"tool {cond.get('tool')} ≠ {tools}"]
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
    """확인 계획이 L3 기대에 맞으면 빈 목록, 아니면 실패 이유 목록."""
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


# ---------------------------------------------------------------- 케이스 형식 검사

def _check_common(rows: list[dict[str, Any]], layer: str, known_rules: set[str]) -> list[str]:
    errs: list[str] = []
    ids = [r["id"] for r in rows]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errs.append(f"{layer} id 중복: {dup}")
    for r in rows:
        if r.get("split") not in SPLITS:
            errs.append(f"{r['id']}: split 은 {SPLITS}")
        if not r.get("rules"):
            errs.append(f"{r['id']}: rules 가 비어 있음")
        for rid in r.get("rules", []):
            if rid not in known_rules:
                errs.append(f"{r['id']}: 규칙 문서에 없는 번호 {rid}")
    return errs


def _check_profile(cid: str, prof: dict[str, Any], uni: str | None, bld: dict[str, set[str]]) -> list[str]:
    errs: list[str] = []
    for key, val in prof.items():
        base = key.split(".")[0]
        if base not in PROFILE_FIELDS:
            errs.append(f"{cid}: 모르는 칸 {key}")
        if base == "university" and val is not None and val not in bld:
            errs.append(f"{cid}: 지원하지 않는 학교 {val}")
        if base == "priorities":
            vals = val if isinstance(val, list) else [val]
            if any(v not in FACTORS for v in vals):
                errs.append(f"{cid}: 우선순위 값 {val}")
        if base == "department_buildings" and val is not None:
            u = prof.get("university", uni)
            bad = [b for b in val if b not in bld.get(u, set())]
            if bad:
                errs.append(f"{cid}: {u} 에 없는 건물 {bad}")
        if base == "larger_area" and val not in ("like", "neutral", "dislike"):
            errs.append(f"{cid}: larger_area 값 {val}")
    return errs


def _check_slots(cid: str, slots: list[dict[str, Any]], uni: str | None, bld: dict[str, set[str]]) -> list[str]:
    errs = []
    for s in slots:
        if s.get("day") not in DAYS or not re.fullmatch(r"\d\d:\d\d", s.get("start", "")) \
                or not re.fullmatch(r"\d\d:\d\d", s.get("end", "")):
            errs.append(f"{cid}: 시간표 칸 형식 {s}")
        if s.get("building") not in bld.get(uni, set()):
            errs.append(f"{cid}: {uni} 에 없는 강의동 {s.get('building')}")
    return errs


def check_l2(rows: list[dict[str, Any]], bld: dict[str, set[str]]) -> list[str]:
    errs: list[str] = []
    for r in rows:
        cid, e, cur = r["id"], r["expect"], r["context"].get("current") or {}
        errs += [f"{cid}: 모르는 기대 키 {k}" for k in set(e) - L2_KEYS]
        errs += [f"{cid}: 모르는 의도 {i}" for i in e.get("intent", []) if i not in INTENTS]
        uni = (e.get("profile") or {}).get("university", cur.get("university"))
        errs += _check_profile(cid, cur, cur.get("university"), bld)
        for key in ("profile", "scenario"):
            plain = {k: v for k, v in (e.get(key) or {}).items() if not isinstance(v, dict)}
            errs += _check_profile(cid, plain, uni, bld)
        for f, v in (e.get("includes") or {}).items():
            if f == "department_buildings" and v not in bld.get(uni, set()):
                errs.append(f"{cid}: {uni} 에 없는 건물 {v}")
        errs += _check_slots(cid, e.get("timetable", []), uni, bld)
        if e.get("unchanged") and (e.get("profile") or e.get("others_unchanged")):
            errs.append(f"{cid}: unchanged 와 profile 변경을 같이 쓸 수 없음")
        if "scenario" in e and not e.get("unchanged"):
            errs.append(f"{cid}: 가정(what_if)은 생활 프로필을 바꾸지 않는다 (unchanged 필요, R-1001)")
    return errs


def check_l3(rows: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []

    def check_matcher(cid: str, m: dict[str, Any]) -> None:
        if "any_of" in m:
            for alt in m["any_of"]:
                check_matcher(cid, alt)
            return
        tools = m["tool"] if isinstance(m["tool"], list) else [m["tool"]]
        for t in tools:
            if t == "amenity" and m.get("amenity") not in AMENITIES:
                errs.append(f"{cid}: 없는 시설 {m.get('amenity')}")
            if t in ("listing_field", "building_field"):
                fields = LISTING_FIELDS if t == "listing_field" else BUILDING_FIELDS
                spec = fields.get(m.get("field", ""))
                if spec is None:
                    errs.append(f"{cid}: {t} 에 없는 칸 {m.get('field')}")
                elif spec.kind == "cat":
                    bad = [p for p in m.get("excludes", []) + m.get("keeps", []) if p not in spec.choices]
                    if bad:
                        errs.append(f"{cid}: {m['field']} 에 없는 값 {bad}")

    for r in rows:
        cid, e = r["id"], r["expect"]
        errs += [f"{cid}: 모르는 기대 키 {k}" for k in set(e) - L3_KEYS]
        errs += [f"{cid}: 없는 현장 확인 {k}" for k in e.get("onsite", []) if k not in ONSITE_CHECKS]
        for m in e.get("conditions", []):
            check_matcher(cid, m)
        ref = r.get("reference")
        if ref is None:
            errs.append(f"{cid}: reference(정답 예시) 없음")
            continue
        try:
            ExclusionPlan.model_validate(ref)
        except Exception as ex:  # noqa: BLE001 — 검증 오류 내용을 그대로 보여 준다
            errs.append(f"{cid}: reference 가 ExclusionPlan 검증 실패: {ex}")
            continue
        errs += [f"{cid}: reference 가 기대를 통과하지 못함 — {w}" for w in match_plan(ref, e)]
    return errs


def check_l4(rows: list[dict[str, Any]], bld: dict[str, set[str]]) -> list[str]:
    errs: list[str] = []
    for r in rows:
        cur = r["context"].get("current") or {}
        errs += _check_profile(r["id"], cur, cur.get("university"), bld)
        if not r.get("turns"):
            errs.append(f"{r['id']}: turns 가 비어 있음")
        for i, t in enumerate(r.get("turns", []), 1):
            e, cid = t["expect"], f"{r['id']}#{i}"
            errs += [f"{cid}: 모르는 기대 키 {k}" for k in set(e) - L4_KEYS]
            errs += [f"{cid}: 모르는 의도 {x}" for x in e.get("intent", []) if x not in INTENTS]
            errs += _check_profile(cid, e.get("profile") or {}, cur.get("university"), bld)
            if e.get("max_questions", 2) > 2:
                errs.append(f"{cid}: 한 번에 2개까지만 묻는다 (R-601)")
    return errs


def check_all() -> list[str]:
    known, bld = rule_ids(), buildings_by_university()
    errs: list[str] = []
    if len(bld) != 4:
        errs.append(f"규칙 문서 R-101 표에서 대학 4개를 읽지 못함: {sorted(bld)}")
    for name, layer, fn in (("l2_extract.jsonl", "L2", lambda rows: check_l2(rows, bld)),
                            ("l3_plan.jsonl", "L3", check_l3),
                            ("l4_dialog.jsonl", "L4", lambda rows: check_l4(rows, bld))):
        rows = load(name)
        errs += _check_common(rows, layer, known) + fn(rows)
    return errs


def summary() -> dict[str, dict[str, int]]:
    out = {}
    for name in ("l2_extract.jsonl", "l3_plan.jsonl", "l4_dialog.jsonl"):
        rows = load(name)
        out[name] = {"전체": len(rows), "평가용": sum(r["split"] == "heldout" for r in rows),
                     "턴": sum(len(r.get("turns", [None])) for r in rows)}
    return out
