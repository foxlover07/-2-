"""CUBIG Syntitan 업로드용 CSV + 우리 쪽 품질 기준선(적용 전) 만들기. 외부로 보내지 않는다 (파일만 만든다).

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.export_syntitan

출력: DB/export/syntitan/<ts>/ (커밋 안 함)
- listings.csv      : 추천에 쓰는 매물 (DATA_CSV_PATH 전체). 원본 링크(source_url)·원본 수집 경로 열은 뺀다
                      (광고 플랫폼 데이터 → 업로드 전 이용 범위 확인 필요, 사용자 승인 후에만 업로드)
- walk_times.csv    : 매물 × 건물 도보시간 (WALK_TABLE_PATH)
- rent_tx.csv       : 국토부 전월세 실거래 (공공데이터, 유형별 최신 파일)
- stores.csv        : 상가(상권)정보 (공공데이터)
- building_titles.csv : 건축물대장 표제부 (공공데이터, 지번별 최신 성공 행)
- baseline_quality.json : 파일별 행 수·열별 결측률·숫자 열 형식 오류·중복 의심·추천 가능 매물 비율
                          → Syntitan 진단(AI Readiness)·정제 '전' 비교 기준 (Experiment E)

원칙: 값을 고치거나 채우지 않는다 (진단 전 원본 그대로). 빈 값은 빈 칸.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT, _read

OUT_ROOT = REPO_ROOT / "DB" / "export" / "syntitan"
DROP_LISTING_COLS = {"source_url", "raw_run", "routes_file"}
NUMERIC = {
    "listings": ["latitude", "longitude", "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon", "area_m2"],
    "walk_times": ["walk_min"],
    "rent_tx": ["area_m2", "deposit_manwon", "monthly_rent_manwon"],
    "stores": ["lat", "lon"],
    "building_titles": ["grnd_flr_cnt", "elevator_cnt", "parking_cnt", "tot_area_m2"],
}


def _paths(env: str) -> list[Path]:
    out = []
    for p in (_read(env) or "").split(","):
        p = p.strip()
        if p:
            pp = Path(p)
            out.append(pp if pp.is_absolute() else (REPO_ROOT / "BE" / pp).resolve())
    return out


def _read_rows(paths: list[Path]) -> tuple[list[str], list[dict[str, str]]]:
    cols: list[str] = []
    rows: list[dict[str, str]] = []
    for p in paths:
        with p.open(encoding="utf-8-sig") as f:
            r = csv.DictReader(f)
            for c in r.fieldnames or []:
                if c not in cols:
                    cols.append(c)
            rows.extend(r)
    return cols, rows


def _write(path: Path, cols: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _is_num(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def quality(name: str, cols: list[str], rows: list[dict[str, str]]) -> dict:
    n = len(rows)
    missing = {c: round(sum(1 for r in rows if (r.get(c) or "").strip() == "") / n, 4) for c in cols} if n else {}
    type_err = {c: sum(1 for r in rows if (r.get(c) or "").strip() and not _is_num(r[c])) for c in NUMERIC.get(name, []) if c in cols}
    q = {"rows": n, "columns": len(cols), "missing_rate": missing, "numeric_type_errors": type_err}
    if name == "listings":
        ids = [r.get("property_id") for r in rows]
        q["duplicate_ids"] = len(ids) - len(set(ids))
        q["duplicate_suspect_rows"] = sum(1 for r in rows if (r.get("duplicate_suspect_group") or "").strip())
        key = lambda r: tuple(r.get(c, "") for c in ("address", "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon", "area_m2"))
        groups: dict[tuple, int] = {}
        for r in rows:
            groups[key(r)] = groups.get(key(r), 0) + 1
        q["same_house_ad_rows"] = sum(v for v in groups.values() if v > 1)
        need = ["monthly_rent_manwon", "maintenance_manwon", "area_m2", "latitude", "longitude"]
        q["recommend_ready_rows"] = sum(1 for r in rows if all((r.get(c) or "").strip() for c in need))
        q["recommend_ready_rate"] = round(q["recommend_ready_rows"] / n, 4) if n else None
        q["by_university"] = {}
        for r in rows:
            q["by_university"][r.get("university", "")] = q["by_university"].get(r.get("university", ""), 0) + 1
    return q


def main() -> None:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = OUT_ROOT / ts
    out.mkdir(parents=True, exist_ok=True)
    staging = REPO_ROOT / "DB" / "staging"
    report: dict = {"ts": ts, "note": "Syntitan 업로드 전 기준선. 값은 원본 그대로 (고치거나 채우지 않음).", "files": {}}

    cols, rows = _read_rows(_paths("DATA_CSV_PATH"))
    cols = [c for c in cols if c not in DROP_LISTING_COLS]
    _write(out / "listings.csv", cols, rows)
    report["files"]["listings.csv"] = {"sources": [str(p.name) for p in _paths("DATA_CSV_PATH")], "dropped_columns": sorted(DROP_LISTING_COLS),
                                       "upload_needs_approval": True, **quality("listings", cols, rows)}

    sources = {
        "walk_times": _paths("WALK_TABLE_PATH"),
        "stores": sorted(staging.glob("stores_*.csv"))[-1:],
        "building_titles": sorted(staging.glob("building_titles_*.csv")),
    }
    # 실거래: 유형별 최신 파일 (app.market 과 같은 규칙)
    from app.market import _tx_sources
    tx_rows: list[dict[str, str]] = []
    tx_cols: list[str] = []
    for path, types in _tx_sources():
        c, rs = _read_rows([path])
        tx_cols = tx_cols or c
        tx_rows.extend(r for r in rs if types is None or r.get("tx_type") in types)
    _write(out / "rent_tx.csv", tx_cols, tx_rows)
    report["files"]["rent_tx.csv"] = {"sources": [p.name for p, _ in _tx_sources()], **quality("rent_tx", tx_cols, tx_rows)}

    for name, paths in sources.items():
        c, rs = _read_rows(paths)
        if name == "building_titles":  # 지번별 최신 성공 행만 (앱과 같은 규칙)
            latest: dict[str, dict[str, str]] = {}
            for r in rs:
                if r.get("match_status") in ("error", "dong_unresolved") and r["lot_key"] in latest:
                    continue
                latest[r["lot_key"]] = r
            rs = list(latest.values())
        _write(out / f"{name}.csv", c, rs)
        report["files"][f"{name}.csv"] = {"sources": [p.name for p in paths], **quality(name, c, rs)}

    (out / "baseline_quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: {"rows": v["rows"], "columns": v["columns"]} for k, v in report["files"].items()}
    print(json.dumps({"out": str(out.relative_to(REPO_ROOT)).replace("\\", "/"), "files": summary,
                      "listings_recommend_ready_rate": report["files"]["listings.csv"]["recommend_ready_rate"],
                      "listings_same_house_ad_rows": report["files"]["listings.csv"]["same_house_ad_rows"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
