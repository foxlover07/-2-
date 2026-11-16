"""온통청년 청년정책 OPEN API → 주거 정책 목록 (정책 **안내**용, 자격 판정 안 함).

사용 (BE/ 에서, 키 발급 후 BE/.env.local 에 YOUTHCENTER_API_KEY 만 넣고):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_policies            # 수집 + 정규화 + 분류 보고서
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_policies --probe    # 1페이지만 요청해 형식 확인 (저장 안 함)
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_policies --from-raw DB/raw/youthcenter/<ts>
                                                                   # 저장된 원본으로 다시 정규화 (API 호출 없음)
    옵션: --max-pages 10 --page-size 100 --delay 0.5

설정 (BE/.env.local, 키 외에는 비워 두면 자동):
- YOUTHCENTER_API_KEY : 온통청년 회원 → 마이페이지 → 오픈(OPEN) API 에서 신청·승인 후 발급 (키는 커밋 금지)
- YOUTHCENTER_API_FORMAT : v2 | v1 | (빈 값 = 자동: v2 → v1 순서로 1페이지를 요청해 정책 행이 나오는 형식을 쓴다)
- YOUTHCENTER_API_URL / YOUTHCENTER_KEY_PARAM / YOUTHCENTER_PAGE_PARAM / YOUTHCENTER_SIZE_PARAM :
  형식 기본값을 덮어쓴다. 키 발급 화면의 명세와 다르면 여기서 바꾼다.
- YOUTHCENTER_EXTRA_PARAMS : 추가 매개변수 (예: "lclsfNm=주거"). 대분류 '주거' 로 좁힐 때.

형식 (2026-11-16 기준 확인 상태):
- v2: https://www.youthcenter.go.kr/go/ythip/getPlcy · apiKeyNm · pageNum · pageSize · rtnType=json
      (2025 개편 주소. 공개 블로그 사례로만 확인, 공공데이터포털 명세는 로그인 필요로 미확인 → --probe 로 확인)
- v1: https://www.youthcenter.go.kr/opi/youthPlcyList.do · openApiVlak · pageIndex · display (옛 공식 예시 주소)
공식 코드정의서(API코드정보.xlsx, 2026-11-15): 정책대분류 lclsfNm '주거', 중분류 mclsfNm
'주택 및 거주지 · 기숙사 · 전월세 및 주거급여 지원'. 응답 필드 이름은 FIELD_CANDIDATES 로 옛·새 이름을 모두 받고,
매핑하지 못한 필드는 report 에 남긴다.

출력: 원본 DB/raw/youthcenter/<ts>/page_<n>.(json|xml) (append-only, 커밋 금지)
      정규화 DB/staging/policies_<ts>.json (+ .report.json: 수집 요약 + 주거 형태 분류 보고) — app.policy 가 읽는다.
요청: 페이지 사이 --delay 초 쉬고, 5xx·시간 초과는 2초 간격 최대 3회 재시도 (공개 사례에서 간헐적 500 보고).
"""

from __future__ import annotations

import argparse
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT, _read

HOUSING_CATEGORY = "주거"
HOUSING_SUBCATEGORIES = ("주택 및 거주지", "기숙사", "전월세 및 주거급여 지원")

FORMATS: dict[str, dict[str, object]] = {
    "v2": {"url": "https://www.youthcenter.go.kr/go/ythip/getPlcy", "key": "apiKeyNm", "page": "pageNum",
           "size": "pageSize", "extra": {"rtnType": "json"}},
    "v1": {"url": "https://www.youthcenter.go.kr/opi/youthPlcyList.do", "key": "openApiVlak", "page": "pageIndex",
           "size": "display", "extra": {}},
}
DEFAULT_URL = FORMATS["v1"]["url"]  # 하위 호환 (옛 이름)

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
    "region_codes": ("zipCd",),                       # 법정시군구코드 목록 (쉼표) → 시·도 이름으로 바꾼다
    "region": ("rgtrUpInstCdNm", "polyBizSecd"),      # 등록 상위기관 이름 (시·도일 때만 지역으로 쓴다)
    "target_age_min": ("sprtTrgtMinAge",),
    "target_age_max": ("sprtTrgtMaxAge",),
    "target_age_text": ("ageInfo",),
    "apply_period": ("aplyYmd", "rqutPrdCn"),
    # 아래 4개는 신청 절차·자격 표기 (판정에 쓰지 않고 그대로 보여 준다). 이름은 첫 응답으로 확인 필요
    "income_text": ("earnEtcCn",),
    "apply_method": ("plcyAplyMthdCn", "rqutProcCn"),
    "documents": ("sbmsnDcmntCn", "pstnPaprCn"),
    "screening": ("srngMthdCn", "jdgnPresCn"),
    "apply_url": ("aplyUrlAddr", "rqutUrla"),
    "ref_url": ("refUrlAddr1", "rfcSiteUrla1"),
    "updated_at": ("lastMdfcnDt", "frstRegDt"),
}
TOTAL_KEYS = ("totCount", "totalCount", "totalCnt", "totCnt")

# 법정동코드 앞 2자리 → 시·도 (행정표준코드). 강원·전북은 특별자치도 전환 전·후 코드 모두.
SIDO_CODES = {
    "11": "서울", "26": "부산", "27": "대구", "28": "인천", "29": "광주", "30": "대전", "31": "울산", "36": "세종",
    "41": "경기", "42": "강원", "51": "강원", "43": "충북", "44": "충남", "45": "전북", "52": "전북", "46": "전남",
    "47": "경북", "48": "경남", "50": "제주",
    "12": "전남광주",  # 2026 전남광주통합특별시 (온통청년 응답 2026-11-16 확인: 옛 29·46 대신 12)
}
ALL_SIDO = set(SIDO_CODES.values())
_SIDO_NAME = re.compile(r"^(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청북|충북|충청남|충남|전라북|전북|전북특별자치|"
                        r"전라남|전남|경상북|경북|경상남|경남|제주)")


def _pick(row: dict, field: str) -> str | None:
    for k in FIELD_CANDIDATES[field]:
        v = row.get(k)
        if v not in (None, "", "-", "null"):
            return str(v).strip()
    return None


def _policy_list(cur: object) -> bool:
    return isinstance(cur, list) and bool(cur) and all(isinstance(x, dict) for x in cur) and \
        any(k in cur[0] for k in FIELD_CANDIDATES["name"])


def parse_rows(text: str) -> list[dict]:
    """JSON 또는 XML 응답 → 정책 행(dict) 목록. 형식을 모르면 빈 목록."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        try:
            data = json.loads(t)
        except json.JSONDecodeError:
            return []
        # 새 형식은 result.youthPolicyList 등 중첩 → 정책처럼 보이는 dict 목록을 찾는다
        stack = [data]
        while stack:
            cur = stack.pop()
            if _policy_list(cur):
                return cur
            if isinstance(cur, dict):
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
        return []
    try:
        root = ET.fromstring(t)
    except ET.ParseError:  # 오류 문구 등 JSON·XML 이 아닌 응답
        return []
    rows = []
    for el in root.iter():
        kids = list(el)
        if kids and any(k.tag in FIELD_CANDIDATES["name"] for k in kids):
            rows.append({k.tag: (k.text or "").strip() for k in kids})
    return rows


def parse_total(text: str) -> int | None:
    """응답의 전체 건수 (JSON 어디든 totCount 등). 없으면 None → 페이지 크기로 끝을 판단."""
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        try:
            stack = [json.loads(t)]
        except json.JSONDecodeError:
            return None
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k in TOTAL_KEYS:
                    if str(cur.get(k, "")).isdigit():
                        return int(cur[k])
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
        return None
    m = re.search(r"<(?:totalCnt|totCount|totalCount)>(\d+)<", t)
    return int(m.group(1)) if m else None


def region_name(row: dict) -> str | None:
    """정책 대상 지역. 법정시군구코드(zipCd)가 있으면 시·도로 바꾸고 모든 시·도면 '전국'.
    없으면 등록 상위기관 이름이 시·도일 때만 그 이름. 중앙부처 등은 None (= 지역 제한 표기 없음)."""
    codes = [c.strip() for c in (_pick(row, "region_codes") or "").split(",") if c.strip()]
    sido = {SIDO_CODES[c[:2]] for c in codes if c[:2] in SIDO_CODES}
    if sido:
        # 시·도 코드가 바뀐 곳(2026 전남광주통합특별시 등)이 있어 15곳 이상이면 전국으로 본다
        return "전국" if sido >= ALL_SIDO or len(sido) >= 15 else ", ".join(sorted(sido))
    org = _pick(row, "region")
    return org if org and _SIDO_NAME.match(org) else None


def normalize(row: dict, fetched_at: str) -> dict:
    lo, hi, text = _pick(row, "target_age_min"), _pick(row, "target_age_max"), _pick(row, "target_age_text")
    age = text or (f"{lo}~{hi}세" if lo and hi and not (lo == "0" and hi == "0") else None)
    kw = _pick(row, "keywords")
    return {
        "policy_id": _pick(row, "policy_id") or "", "name": _pick(row, "name") or "",
        "summary": _pick(row, "summary"), "support": _pick(row, "support"),
        "category": _pick(row, "category"), "subcategory": _pick(row, "subcategory"),
        "keywords": [k.strip() for k in (kw or "").split(",") if k.strip()],
        "agency": _pick(row, "agency"), "region": region_name(row), "target_age": age,
        "apply_period": _pick(row, "apply_period"), "income_text": _pick(row, "income_text"),
        "apply_method": _pick(row, "apply_method"), "documents": _pick(row, "documents"),
        "screening": _pick(row, "screening"), "apply_url": _pick(row, "apply_url"),
        "ref_url": _pick(row, "ref_url"), "updated_at": _pick(row, "updated_at"), "fetched_at": fetched_at,
    }


def is_housing(item: dict) -> bool:
    """대분류가 '주거' 이거나 중분류가 주거 3종. 분류 필드가 없으면 이름·키워드로 (보수적으로) 판단."""
    if item.get("category"):
        return item["category"] == HOUSING_CATEGORY or (item.get("subcategory") in HOUSING_SUBCATEGORIES)
    text = f"{item.get('name', '')} {' '.join(item.get('keywords', []))}"
    return any(w in text for w in ("주거", "월세", "전세", "임대", "주택", "기숙사"))


def dedupe(items: list[dict]) -> list[dict]:
    """같은 policy_id 는 하나만 (페이지 경계 중복 대비). id 가 없으면 이름으로."""
    seen: set[str] = set()
    out = []
    for x in items:
        k = x["policy_id"] or x["name"]
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


# ---------------------------------------------------------------- 요청

def _config(fmt: str) -> dict[str, object]:
    base = dict(FORMATS[fmt])
    for name, env in (("url", "YOUTHCENTER_API_URL"), ("key", "YOUTHCENTER_KEY_PARAM"),
                      ("page", "YOUTHCENTER_PAGE_PARAM"), ("size", "YOUTHCENTER_SIZE_PARAM")):
        v = (_read(env) or "").strip()
        if v:
            base[name] = v
    extra = dict(base["extra"])  # type: ignore[arg-type]
    extra.update(dict(x.split("=", 1) for x in (_read("YOUTHCENTER_EXTRA_PARAMS") or "").split("&") if "=" in x))
    base["extra"] = extra
    base["format"] = fmt
    return base


def fetch_page(client: httpx.Client, cfg: dict, key: str, page: int, size: int, tries: int = 3) -> httpx.Response:
    params = {cfg["key"]: key, cfg["page"]: str(page), cfg["size"]: str(size), **cfg["extra"]}  # type: ignore[dict-item]
    last: Exception | None = None
    for i in range(tries):
        try:
            r = client.get(str(cfg["url"]), params=params)
            if r.status_code < 500:
                return r
            last = RuntimeError(f"HTTP {r.status_code}")
        except httpx.TransportError as e:  # 시간 초과·연결 끊김
            last = e
        if i < tries - 1:
            time.sleep(2)
    raise SystemExit(f"요청 실패 ({cfg['url']} {cfg['page']}={page}): {last} — 잠시 뒤 다시 실행해 주세요.")


def _mask(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def choose_format(client: httpx.Client, key: str, size: int) -> tuple[dict, httpx.Response]:
    """YOUTHCENTER_API_FORMAT 이 없으면 v2 → v1 순서로 1페이지를 요청해 정책 행이 나오는 형식을 고른다."""
    fixed = (_read("YOUTHCENTER_API_FORMAT") or "").strip()
    order = [fixed] if fixed else ["v2", "v1"]
    tried = []
    for fmt in order:
        if fmt not in FORMATS:
            raise SystemExit(f"YOUTHCENTER_API_FORMAT={fmt!r} 는 지원하지 않습니다 (v2 | v1).")
        cfg = _config(fmt)
        r = fetch_page(client, cfg, key, 1, size)
        rows = parse_rows(r.text) if r.status_code < 400 else []
        if rows:
            return cfg, r
        tried.append(f"{fmt}: HTTP {r.status_code}, 정책 행 0개 — 응답 앞부분: {_mask(r.text[:200], key)!r}")
    raise SystemExit("정책 행을 받지 못했습니다 (키 승인 상태·주소·매개변수 이름 확인).\n" + "\n".join(tried))


# ---------------------------------------------------------------- 실행

def build_outputs(rows: list[dict], fetched_at: str, meta: dict, seen_fields: set[str],
                  out_dir: Path | None = None) -> dict:
    from pipeline.policy_report import classification_report

    items = dedupe([normalize(x, fetched_at) for x in rows])
    housing = [x for x in items if x["name"] and is_housing(x)]
    mapped = {c for cands in FIELD_CANDIDATES.values() for c in cands}
    ts = meta["ts"]
    out = (out_dir or REPO_ROOT / "DB" / "staging") / f"policies_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": "온통청년 청년정책 OPEN API", "fetched_at": fetched_at, "items": housing},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    report = {**meta, "rows": len(rows), "unique": len(items), "housing": len(housing),
              "unmapped_fields": sorted(seen_fields - mapped),
              "missing_rate": {f: round(sum(1 for x in housing if not x.get(f)) / len(housing), 3) if housing else None
                               for f in ("summary", "support", "region", "apply_url", "ref_url", "apply_period")},
              "output": str(out.relative_to(REPO_ROOT) if out.is_relative_to(REPO_ROOT) else out).replace("\\", "/"),
              "classification": classification_report(housing),
              "note": "unmapped_fields 를 보고 FIELD_CANDIDATES 를, classification.unclassified 를 보고 "
                      "app.policy.HOUSING_TYPE_RULES 낱말을 맞춘다."}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def from_raw(raw_dir: Path, out_dir: Path | None = None) -> dict:
    """저장된 원본 페이지로 다시 정규화 (API 호출 없음). FIELD_CANDIDATES·분류 규칙을 고친 뒤 쓴다."""
    files = sorted(raw_dir.glob("page_*.*"), key=lambda f: int(re.sub(r"\D", "", f.stem) or 0))
    if not files:
        raise SystemExit(f"{raw_dir} 에 page_*.json|xml 이 없습니다.")
    rows: list[dict] = []
    seen: set[str] = set()
    for f in files:
        got = parse_rows(f.read_text(encoding="utf-8"))
        for g in got:
            seen |= set(g)
        rows += got
    mtime = datetime.fromtimestamp(files[0].stat().st_mtime).astimezone().isoformat(timespec="seconds")
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return build_outputs(rows, mtime, {"ts": ts, "from_raw": str(raw_dir), "pages": len(files)}, seen, out_dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--delay", type=float, default=0.5, help="페이지 사이 쉬는 시간(초)")
    ap.add_argument("--probe", action="store_true", help="1페이지만 요청해 형식·필드만 확인 (파일 저장 안 함)")
    ap.add_argument("--from-raw", help="저장된 원본 폴더로 다시 정규화 (API 호출 없음)")
    args = ap.parse_args()

    if args.from_raw:
        p = Path(args.from_raw)
        print(json.dumps(from_raw(p if p.is_absolute() else (REPO_ROOT / p)), ensure_ascii=False, indent=2))
        return

    key = (_read("YOUTHCENTER_API_KEY") or "").strip()
    if not key:
        raise SystemExit("YOUTHCENTER_API_KEY 가 없습니다 (온통청년 마이페이지 → 오픈(OPEN) API 에서 발급, 승인 필요).")

    with httpx.Client(timeout=30) as client:
        cfg, first = choose_format(client, key, args.page_size)
        if args.probe:
            rows = parse_rows(first.text)
            mapped = {c for cands in FIELD_CANDIDATES.values() for c in cands}
            print(json.dumps({"format": cfg["format"], "url": cfg["url"], "total": parse_total(first.text),
                              "rows_page1": len(rows), "fields": sorted(rows[0]) if rows else [],
                              "unmapped_fields": sorted(set(rows[0]) - mapped) if rows else [],
                              "sample": normalize(rows[0], "probe") if rows else None}, ensure_ascii=False, indent=2))
            return

        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        raw_dir = REPO_ROOT / "DB" / "raw" / "youthcenter" / ts
        raw_dir.mkdir(parents=True, exist_ok=True)
        total = parse_total(first.text)
        rows: list[dict] = []
        seen_fields: set[str] = set()
        pages = 0
        r = first
        for page in range(1, args.max_pages + 1):
            if page > 1:
                time.sleep(args.delay)
                r = fetch_page(client, cfg, key, page, args.page_size)
            pages += 1
            ext = "json" if r.text.lstrip()[:1] in "{[" else "xml"
            (raw_dir / f"page_{page}.{ext}").write_text(r.text, encoding="utf-8")
            if r.status_code >= 400:
                raise SystemExit(f"HTTP {r.status_code} — 원본은 {raw_dir} (키 승인 상태·주소 확인)")
            got = parse_rows(r.text)
            for g in got:
                seen_fields |= set(g)
            rows += got
            if len(got) < args.page_size or (total is not None and len(rows) >= total):
                break

    meta = {"ts": ts, "format": cfg["format"], "url": cfg["url"], "pages": pages, "total_reported": total,
            "truncated": bool(total is not None and len(rows) < total)}
    report = build_outputs(rows, fetched_at, meta, seen_fields)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["truncated"]:
        print(f"주의: 전체 {total}건 중 {len(rows)}건만 받았습니다. --max-pages 를 늘리거나 "
              "YOUTHCENTER_EXTRA_PARAMS=lclsfNm=주거 로 좁혀 다시 실행하세요.")


if __name__ == "__main__":
    main()
