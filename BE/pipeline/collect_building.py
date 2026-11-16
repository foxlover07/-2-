"""건축물대장 표제부 수집 (국토교통부 건축HUB, 공공데이터포털) → 매물 지번과 연결.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_building [--max-calls 800] [--csv a.csv,b.csv]
        [--retry ../DB/staging/building_titles_<ts>.csv]   # 이전 결과에서 error 인 지번만 다시

- 매물 주소(지번, location_precision=lot)의 시군구코드·법정동코드·본번·부번으로 getBrTitleInfo 를 부른다.
  법정동코드는 이미 수집한 상가(상권)정보 원본(DB/raw/sbiz)의 ldongCd 로 (구, 동 이름) → 코드를 만든다 (추가 호출 없음).
- 같은 지번은 한 번만 부른다. 원본 JSON 은 DB/raw/bldrgst/<ts>/ 에 그대로 둔다 (append-only, 커밋 금지).
- 결과: DB/staging/building_titles_<ts>.csv (지번 1행) + .report.json
  · match_status: matched(주건축물 1개) · multiple(주건축물 여러 개 — 어느 건물인지 모름, 값은 비움) · no_record · dong_unresolved · error
  · 한 지번에 건물이 여러 개면 임의로 고르지 않는다.
- 값이 없으면 빈 칸 — 0 으로 채우지 않는다 (승강기·주차 0 은 대장에 0 으로 적힌 값).
- 광고 매물의 지번이 실제 건물과 다를 수 있다 (광고 주소 정확도). 화면에는 '대장 기준 참고 정보'로만 쓴다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT, _read
from app.publicdata import PublicDataError, get

DB = REPO_ROOT / "DB"
URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
SGG_CODES = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200", "동대문구": "11230", "중랑구": "11260",
    "성북구": "11290", "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380", "서대문구": "11410",
    "마포구": "11440",
}
ADDR_RE = re.compile(r"(\S+구)\s+(\S+?)\s+(산\s*)?(\d+)(?:-(\d+))?\s*$")

FIELDS = [
    "lot_key", "address", "sigungu_cd", "bjdong_cd", "plat_gb_cd", "bun", "ji", "match_status", "n_records", "n_main",
    "bld_nm", "main_purps", "etc_purps", "use_apr_day", "grnd_flr_cnt", "ugrnd_flr_cnt", "elevator_cnt",
    "hhld_cnt", "fmly_cnt", "ho_cnt", "strct", "tot_area_m2", "parking_cnt", "mgm_bldrgst_pk",
    "source", "fetched_at", "raw_file", "error",
]
SOURCE = "국토교통부 건축HUB 건축물대장 표제부 (공공데이터포털)"


def dong_codes(sbiz_dir: Path) -> dict[tuple[str, str], str]:
    """상가정보 원본에서 (시군구코드, 법정동 이름) → 법정동코드 10자리."""
    out: dict[tuple[str, str], str] = {}
    for f in sbiz_dir.rglob("*.json"):
        try:
            items = json.loads(f.read_text(encoding="utf-8")).get("body", {}).get("items") or []
        except (OSError, ValueError):
            continue
        for it in items:
            if it.get("signguCd") and it.get("ldongNm") and it.get("ldongCd"):
                out[(it["signguCd"], it["ldongNm"])] = it["ldongCd"]
    return out


def parse_lot(address: str, codes: dict[tuple[str, str], str]) -> dict | None:
    """'서울특별시 서대문구 대현동 53-33' → 조회 키. 지번 형식이 아니면 None, 동 코드를 모르면 bjdong_cd ''."""
    m = ADDR_RE.search(address or "")
    if not m:
        return None
    sgg = SGG_CODES.get(m.group(1))
    if sgg is None:
        return None
    code = codes.get((sgg, m.group(2)), "")
    return {"sigungu_cd": sgg, "bjdong_cd": code[5:] if code else "", "plat_gb_cd": "1" if m.group(3) else "0",
            "bun": f"{int(m.group(4)):04d}", "ji": f"{int(m.group(5) or 0):04d}", "dong_nm": m.group(2)}


def lot_key(q: dict) -> str:
    return f"{q['sigungu_cd']}-{q['bjdong_cd'] or q['dong_nm']}-{q['plat_gb_cd']}-{q['bun']}-{q['ji']}"


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sum(it: dict, keys: list[str]) -> int | None:
    vals = [_int(it.get(k)) for k in keys]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)


def summarize(items: list[dict]) -> dict:
    """표제부 목록 → 1행. 주건축물이 정확히 1개일 때만 값을 채운다."""
    main = [it for it in items if (it.get("mainAtchGbCdNm") or "").strip() == "주건축물"] or items
    row = {"n_records": len(items), "n_main": len(main)}
    if not items:
        return {**row, "match_status": "no_record"}
    if len(main) > 1:
        return {**row, "match_status": "multiple"}
    it = main[0]
    return {
        **row, "match_status": "matched",
        "bld_nm": (it.get("bldNm") or "").strip(), "main_purps": (it.get("mainPurpsCdNm") or "").strip(),
        "etc_purps": (it.get("etcPurps") or "").strip(), "use_apr_day": str(it.get("useAprDay") or "").strip(),
        "grnd_flr_cnt": _int(it.get("grndFlrCnt")), "ugrnd_flr_cnt": _int(it.get("ugrndFlrCnt")),
        "elevator_cnt": _sum(it, ["rideUseElvtCnt", "emgenUseElvtCnt"]),
        "hhld_cnt": _int(it.get("hhldCnt")), "fmly_cnt": _int(it.get("fmlyCnt")), "ho_cnt": _int(it.get("hoCnt")),
        "strct": (it.get("strctCdNm") or "").strip(), "tot_area_m2": it.get("totArea"),
        "parking_cnt": _sum(it, ["indrMechUtcnt", "indrAutoUtcnt", "oudrMechUtcnt", "oudrAutoUtcnt"]),
        "mgm_bldrgst_pk": str(it.get("mgmBldrgstPk") or ""),
    }


def fetch(q: dict, client: httpx.Client) -> tuple[str, list[dict]]:
    params = {"sigunguCd": q["sigungu_cd"], "bjdongCd": q["bjdong_cd"], "platGbCd": q["plat_gb_cd"],
              "bun": q["bun"], "ji": q["ji"], "_type": "json", "numOfRows": "100", "pageNo": "1"}
    text = get(URL, params, client=client)
    try:
        body = json.loads(text)["response"]["body"]
    except (ValueError, KeyError) as e:
        raise PublicDataError("JSON 응답이 아닙니다", "bad_response") from e
    raw = body.get("items") or {}
    items = raw.get("item", []) if isinstance(raw, dict) else []
    return text, items if isinstance(items, list) else [items]


def listing_csvs(arg: str | None) -> list[Path]:
    value = arg or _read("DATA_CSV_PATH") or ""
    out = []
    for p in value.split(","):
        p = p.strip()
        if p:
            pp = Path(p)
            out.append(pp if pp.is_absolute() else (REPO_ROOT / "BE" / pp).resolve())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="매물 CSV (쉼표). 기본 DATA_CSV_PATH")
    ap.add_argument("--max-calls", type=int, default=800)
    ap.add_argument("--sleep", type=float, default=0.1)
    ap.add_argument("--retry", help="이전 결과 CSV: 그 파일에서 error 인 지번만 다시 부른다 (앱은 지번마다 가장 최근 성공 행을 쓴다)")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    raw_dir = DB / "raw" / "bldrgst" / ts
    raw_dir.mkdir(parents=True, exist_ok=True)
    codes = dong_codes(DB / "raw" / "sbiz")

    lots: dict[str, tuple[dict, str]] = {}
    n_listings = 0
    skipped_format = 0
    for path in listing_csvs(args.csv):
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            n_listings += 1
            if (r.get("location_precision") or "lot") != "lot":
                skipped_format += 1
                continue
            q = parse_lot(r["address"], codes)
            if q is None:
                skipped_format += 1
                continue
            lots.setdefault(lot_key(q), (q, r["address"]))

    if args.retry:
        with Path(args.retry).open(encoding="utf-8-sig") as f:
            again = {r["lot_key"] for r in csv.DictReader(f) if r["match_status"] == "error"}
        lots = {k: v for k, v in lots.items() if k in again}

    rows: list[dict] = []
    calls = 0
    stop_reason = None
    with httpx.Client(timeout=30) as client:
        for key, (q, address) in lots.items():
            base = {"lot_key": key, "address": address, **{k: q[k] for k in ("sigungu_cd", "bjdong_cd", "plat_gb_cd", "bun", "ji")},
                    "source": SOURCE, "fetched_at": fetched_at}
            if not q["bjdong_cd"]:
                rows.append({**base, "match_status": "dong_unresolved", "error": f"법정동코드 모름: {q['dong_nm']}"})
                continue
            if stop_reason or calls >= args.max_calls:
                stop_reason = stop_reason or "max_calls"
                continue
            try:
                calls += 1
                text, items = fetch(q, client)
            except PublicDataError as e:
                rows.append({**base, "match_status": "error", "error": f"{e.kind}: {e}"})
                if e.kind in ("limit", "no_key", "not_registered"):
                    stop_reason = e.kind
                continue
            raw_file = raw_dir / f"{key}.json"
            raw_file.write_text(text, encoding="utf-8")
            rows.append({**base, **summarize(items), "raw_file": str(raw_file.relative_to(REPO_ROOT)).replace("\\", "/")})
            time.sleep(args.sleep)

    out = DB / "staging" / f"building_titles_{ts}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["match_status"]] = by_status.get(r["match_status"], 0) + 1
    report = {"ts": ts, "source": SOURCE, "listings": n_listings, "skipped_not_lot": skipped_format, "unique_lots": len(lots),
              "calls": calls, "rows": len(rows), "not_called": len(lots) - len(rows), "by_status": by_status,
              "stop_reason": stop_reason, "output": str(out.relative_to(REPO_ROOT)).replace("\\", "/")}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
