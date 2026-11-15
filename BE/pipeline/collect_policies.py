"""온통청년 청년정책 OPEN API → 주거 정책 목록 (정책 **안내**용, 자격 판정 안 함).

사용 (BE/ 에서, 키 발급 후):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_policies [--max-pages 5] [--page-size 100]

설정 (BE/.env.local):
- YOUTHCENTER_API_KEY : 온통청년 회원 → 마이페이지 → 오픈(OPEN) API 에서 신청·승인 후 발급 (키는 커밋 금지)
- YOUTHCENTER_API_URL : 요청 주소. 기본값은 공식 안내 페이지 예시 주소(https://www.youthcenter.go.kr/opi/youthPlcyList.do).
                        발급 후 마이페이지의 명세와 다르면 여기서 바꾼다.
- YOUTHCENTER_KEY_PARAM / YOUTHCENTER_PAGE_PARAM / YOUTHCENTER_SIZE_PARAM : 매개변수 이름 (기본: 예시의 openApiVlak · pageIndex · display)
- YOUTHCENTER_EXTRA_PARAMS : 추가 매개변수 (예: "lclsfNm=주거" 또는 "query=주거"). 정책 대분류 '주거' 로 좁힐 때.

확인된 것 (공식 코드정의서 API코드정보.xlsx, 2026-11-15): 정책대분류 lclsfNm 에 '주거', 중분류 mclsfNm 에
'주택 및 거주지 · 기숙사 · 전월세 및 주거급여 지원'. 응답 필드 이름은 키 발급 후 실제 응답으로 확인한다 →
아래 FIELD_CANDIDATES 로 옛 형식·새 형식 이름을 모두 받아 보고, 매핑하지 못한 필드는 report 에 남긴다.

출력: 원본 DB/raw/youthcenter/<ts>/page_<n>.(json|xml) (append-only, 커밋 금지)
      정규화 DB/staging/policies_<ts>.json (+ report) — app.policy 가 읽는다.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from app.config import REPO_ROOT, _read

DEFAULT_URL = "https://www.youthcenter.go.kr/opi/youthPlcyList.do"
HOUSING_CATEGORY = "주거"
HOUSING_SUBCATEGORIES = ("주택 및 거주지", "기숙사", "전월세 및 주거급여 지원")

# 정규화 필드 → 원본 필드 후보 (앞에 있는 것부터). 실제 응답으로 확인 필요.
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "policy_id": ("plcyNo", "bizId", "policyId"),
    "name": ("plcyNm", "polyBizSjnm", "policyName"),
    "summary": ("plcyExplnCn", "polyItcnCn"),
    "support": ("plcySprtCn", "sporCn"),
    "category": ("lclsfNm", "polyRlmCd"),
    "subcategory": ("mclsfNm",),
    "keywords": ("plcyKywdNm",),
    "agency": ("operInstCdNm", "sprvsnInstCdNm", "cnsgNmor", "mngtMson"),
    "region": ("rgtrUpInstCdNm", "zipCd", "polyBizSecd"),
    "target_age_min": ("sprtTrgtMinAge",),
    "target_age_max": ("sprtTrgtMaxAge",),
    "target_age_text": ("ageInfo",),
    "apply_period": ("aplyYmd", "rqutPrdCn"),
    "apply_url": ("aplyUrlAddr", "rqutUrla"),
    "ref_url": ("refUrlAddr1", "rfcSiteUrla1"),
    "updated_at": ("lastMdfcnDt", "frstRegDt"),
}


def _pick(row: dict, field: str) -> str | None:
    for k in FIELD_CANDIDATES[field]:
        v = row.get(k)
        if v not in (None, "", "-", "null"):
            return str(v).strip()
    return None


def parse_rows(text: str) -> list[dict]:
    """JSON 또는 XML 응답 → 정책 행(dict) 목록. 형식을 모르면 빈 목록."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        data = json.loads(t)
        # 새 형식은 result.youthPolicyList 등 중첩 → 정책처럼 보이는 dict 목록을 찾는다
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list) and cur and all(isinstance(x, dict) for x in cur) and \
                    any(k in cur[0] for c in FIELD_CANDIDATES["name"] for k in [c]):
                return cur
            if isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
        return []
    root = ET.fromstring(t)
    rows = []
    for el in root.iter():
        kids = list(el)
        if kids and any(k.tag in FIELD_CANDIDATES["name"] for k in kids):
            rows.append({k.tag: (k.text or "").strip() for k in kids})
    return rows


def normalize(row: dict, fetched_at: str) -> dict:
    lo, hi, text = _pick(row, "target_age_min"), _pick(row, "target_age_max"), _pick(row, "target_age_text")
    age = text or (f"{lo}~{hi}세" if lo and hi and not (lo == "0" and hi == "0") else None)
    kw = _pick(row, "keywords")
    return {
        "policy_id": _pick(row, "policy_id") or "", "name": _pick(row, "name") or "",
        "summary": _pick(row, "summary"), "support": _pick(row, "support"),
        "category": _pick(row, "category"), "subcategory": _pick(row, "subcategory"),
        "keywords": [k.strip() for k in (kw or "").split(",") if k.strip()],
        "agency": _pick(row, "agency"), "region": _pick(row, "region"), "target_age": age,
        "apply_period": _pick(row, "apply_period"), "apply_url": _pick(row, "apply_url"),
        "ref_url": _pick(row, "ref_url"), "updated_at": _pick(row, "updated_at"), "fetched_at": fetched_at,
    }


def is_housing(item: dict) -> bool:
    """대분류가 '주거' 이거나 중분류가 주거 3종. 분류 필드가 없으면 이름·키워드로 (보수적으로) 판단."""
    if item.get("category"):
        return item["category"] == HOUSING_CATEGORY or (item.get("subcategory") in HOUSING_SUBCATEGORIES)
    text = f"{item.get('name', '')} {' '.join(item.get('keywords', []))}"
    return any(w in text for w in ("주거", "월세", "전세", "임대", "주택", "기숙사"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--page-size", type=int, default=100)
    args = ap.parse_args()
    key = (_read("YOUTHCENTER_API_KEY") or "").strip()
    if not key:
        raise SystemExit("YOUTHCENTER_API_KEY 가 없습니다 (온통청년 마이페이지 → 오픈(OPEN) API 에서 발급, 승인 필요).")
    url = (_read("YOUTHCENTER_API_URL") or DEFAULT_URL).strip()
    kp = (_read("YOUTHCENTER_KEY_PARAM") or "openApiVlak").strip()
    pp = (_read("YOUTHCENTER_PAGE_PARAM") or "pageIndex").strip()
    sp = (_read("YOUTHCENTER_SIZE_PARAM") or "display").strip()
    extra = dict(x.split("=", 1) for x in (_read("YOUTHCENTER_EXTRA_PARAMS") or "").split("&") if "=" in x)

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    raw_dir = REPO_ROOT / "DB" / "raw" / "youthcenter" / ts
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    pages = 0
    seen_fields: set[str] = set()
    with httpx.Client(timeout=30) as client:
        for page in range(1, args.max_pages + 1):
            r = client.get(url, params={kp: key, pp: str(page), sp: str(args.page_size), **extra})
            pages += 1
            ext = "json" if r.text.lstrip()[:1] in "{[" else "xml"
            (raw_dir / f"page_{page}.{ext}").write_text(r.text, encoding="utf-8")
            if r.status_code >= 400:
                raise SystemExit(f"HTTP {r.status_code} — 원본은 {raw_dir} (키 승인 상태·주소 확인)")
            got = parse_rows(r.text)
            for g in got:
                seen_fields |= set(g)
            rows += got
            if len(got) < args.page_size:
                break

    items = [normalize(x, fetched_at) for x in rows]
    housing = [x for x in items if x["name"] and is_housing(x)]
    mapped = {c for cands in FIELD_CANDIDATES.values() for c in cands}
    out = REPO_ROOT / "DB" / "staging" / f"policies_{ts}.json"
    out.write_text(json.dumps({"source": "온통청년 청년정책 OPEN API", "fetched_at": fetched_at, "items": housing},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"ts": ts, "url": url, "pages": pages, "rows": len(rows), "housing": len(housing),
              "unmapped_fields": sorted(seen_fields - mapped), "output": str(out.relative_to(REPO_ROOT)).replace("\\", "/"),
              "note": "unmapped_fields 를 보고 FIELD_CANDIDATES 를 실제 응답에 맞춘다 (첫 실행 후 확인)."}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
