"""통합 매물 데이터셋 — 매물 1건 = 1행에 도보·실거래·편의시설·건축물대장을 붙인다.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.build_integrated [--export]

- 입력: DATA_CSV_PATH(매물), WALK_TABLE_PATH(도보시간), DB/staging 의 rent_tx_* · stores_* · building_titles_* · poi_*
- 매물 상세(방 구조·해당 층·건물 층수)는 추천용 후보 CSV 에 없어서 정규화 매물(DB/staging/*listings_v*.csv)에서
  (대학, 출처, 원본 매물 ID) 로 붙인다. 같은 매물이 여러 버전에 있으면 조회 시점이 같은 행 → 없으면 가장 최근 버전.
- 계산은 서비스 코드와 같은 함수를 쓴다 (app.market.compare_property, app.amenities.amenities_for,
  app.building_register.building_for) → 화면·API·챗봇에 나오는 값과 같다.
- 값이 없으면 빈 칸 (0 으로 채우지 않는다). 계산하지 못한 이유는 *_reason 열에 남긴다.
- 출력: DB/staging/integrated_listings_<ts>.csv + .report.json (커밋 안 함)
  --export: DB/export/syntitan/<ts>/integrated_listings.csv 로도 복사 (원본 링크·수집 경로 열 제외, 업로드용)
- 추천 점수·순위는 넣지 않는다 (사용자 조건마다 달라서). 참고 정보다.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime

from app.amenities import CATEGORY_INFO, amenities_for
from app.building_register import building_for
from app.config import REPO_ROOT
from app.data import load_properties, load_travel_times
from app.market import compare_property
from app.poi import KINDS as POI_KINDS
from app.poi import nearby_pois

DROP_FOR_EXPORT = {"source_url", "raw_run", "routes_file"}
LISTING_DETAIL_COLS = ["room_type", "floor", "building_floor"]


def load_listing_details() -> dict[tuple[str, str, str], list[dict]]:
    """정규화 매물 행 (대학, 출처, 원본 ID) → 행 목록 (오래된 버전 → 최근 버전 순)."""
    out: dict[tuple[str, str, str], list[dict]] = {}
    for path in sorted((REPO_ROOT / "DB" / "staging").glob("*listings_v*.csv")):
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if "room_type" not in r:
                    continue
                out.setdefault((r.get("university", ""), r.get("source", ""), r.get("source_listing_id", "")), []).append(r)
    return out


def listing_detail(details: dict, r: dict) -> dict | None:
    rows = details.get((r.get("university", ""), r.get("source", ""), r.get("source_listing_id", "")))
    if not rows:
        return None
    same = [x for x in rows if x.get("listing_checked_at") == r.get("listing_checked_at")]
    return (same or rows)[-1]


def _same_house_key(r: dict) -> tuple | None:
    parts = tuple(r.get(c, "") for c in ("address", "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon", "area_m2"))
    return None if any(v in ("", None) for v in parts) else parts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="Syntitan 업로드 폴더에도 복사 (링크 열 제외)")
    args = ap.parse_args()

    # 매물 원본 행 (Property 모델에 없는 출처 열까지 유지)
    from app.data import _csv_paths  # noqa: PLC0415 — 매물 CSV 경로 (DATA_CSV_PATH)
    raw_rows: dict[str, dict] = {}
    base_cols: list[str] = []
    for path in _csv_paths():
        with path.open(encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            for c in rd.fieldnames or []:
                if c not in base_cols:
                    base_cols.append(c)
            for r in rd:
                raw_rows.setdefault(r["property_id"], r)

    props = load_properties()
    travel = load_travel_times(props)
    groups: dict[tuple, int] = {}
    for r in raw_rows.values():
        k = _same_house_key(r)
        if k:
            groups[k] = groups.get(k, 0) + 1

    cats = list(CATEGORY_INFO)
    details = load_listing_details()
    poi_cols = []
    for k, (_, radii) in POI_KINDS.items():
        poi_cols += [f"nearest_m_{k}", f"nearest_{k}_name"] + ([f"nearest_{k}_detail"] if k != "bus_stop" else [])             + [f"count_{r}m_{k}" for r in radii]
    extra = (LISTING_DETAIL_COLS + ["listing_detail_status", "monthly_cost_manwon", "walk_buildings_n", "walk_min_nearest", "walk_nearest_building", "same_house_ads_n",
              "market_available", "market_scope", "market_n", "market_rent_median", "market_rent_p25", "market_rent_p75",
              "market_listing_percentile", "market_level", "market_reason"]
             + [f"nearest_m_{c}" for c in cats] + [f"count_500m_{c}" for c in cats] + ["amenities_reason"]
             + poi_cols + ["poi_reason"]
             + ["bld_status", "bld_name", "bld_main_purpose", "bld_use_approval_year", "bld_floors_above", "bld_elevators",
                "bld_parking", "bld_households", "bld_structure", "bld_reason"])

    out_rows = []
    stats = {"market_available": 0, "amenities_available": 0, "building_matched": 0, "poi_available": 0,
             "listing_detail_matched": 0}
    for p in props:
        r = dict(raw_rows.get(p.property_id, {}))
        cost = None if p.monthly_rent_manwon is None or p.maintenance_manwon is None else p.monthly_rent_manwon + p.maintenance_manwon
        times = travel.get(p.property_id, {})
        near = min(times.items(), key=lambda kv: kv[1]) if times else None
        k = _same_house_key(r)
        m = compare_property(p)
        a = amenities_for(p)
        b = building_for(p)
        pr = nearby_pois(p)
        d = listing_detail(details, r)
        stats["poi_available"] += pr.available
        stats["listing_detail_matched"] += d is not None
        pk = {s.kind: s for s in pr.kinds}
        poi_vals: dict = {}
        for k, (_, radii) in POI_KINDS.items():
            s = pk.get(k)
            n = s.nearest if s else None
            poi_vals[f"nearest_m_{k}"] = n.distance_m if n else None
            poi_vals[f"nearest_{k}_name"] = n.name if n else None
            if k != "bus_stop":
                poi_vals[f"nearest_{k}_detail"] = n.detail if n else None
            for rad in radii:
                poi_vals[f"count_{rad}m_{k}"] = s.counts.get(str(rad)) if s else None
        stats["market_available"] += m.available
        stats["amenities_available"] += a.available
        stats["building_matched"] += b.available
        by = {c.category: c for c in a.categories}
        r.update({
            **{c: ((d.get(c) or None) if d else None) for c in LISTING_DETAIL_COLS},
            "listing_detail_status": "matched" if d else "not_found",
            "monthly_cost_manwon": cost,
            "walk_buildings_n": len(times),
            "walk_min_nearest": near[1] if near else None,
            "walk_nearest_building": near[0] if near else None,
            "same_house_ads_n": groups.get(k, 1) if k else None,
            "market_available": m.available, "market_scope": m.scope, "market_n": m.n if m.available else None,
            "market_rent_median": m.rent_median, "market_rent_p25": m.rent_p25, "market_rent_p75": m.rent_p75,
            "market_listing_percentile": m.listing_percentile, "market_level": m.level, "market_reason": m.reason,
            **{f"nearest_m_{c}": (by[c].nearest.distance_m if c in by and by[c].nearest else None) for c in cats},
            **{f"count_500m_{c}": (by[c].counts.get("500") if c in by else None) for c in cats},
            "amenities_reason": a.reason,
            **poi_vals, "poi_reason": pr.reason,
            "bld_status": b.match_status, "bld_name": b.building_name, "bld_main_purpose": b.main_purpose,
            "bld_use_approval_year": b.use_approval_year, "bld_floors_above": b.floors_above, "bld_elevators": b.elevators,
            "bld_parking": b.parking, "bld_households": b.households, "bld_structure": b.structure, "bld_reason": b.reason,
        })
        out_rows.append(r)

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    cols = base_cols + [c for c in extra if c not in base_cols]
    out = REPO_ROOT / "DB" / "staging" / f"integrated_listings_{ts}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    n = len(out_rows)
    report = {
        "ts": ts, "rows": n, "columns": len(cols), "output": str(out.relative_to(REPO_ROOT)).replace("\\", "/"),
        "sources": {"listings": [p.name for p in _csv_paths()]},
        "coverage": {k: {"rows": v, "rate": round(v / n, 4) if n else None} for k, v in stats.items()},
        "by_university": {},
        "note": "추천 점수·순위는 사용자 조건마다 달라 넣지 않음. 실거래는 과거 계약(참고), 편의시설·교통·공원은 직선거리"
                "(대표점 기준), 건축물대장은 광고 지번 기준. floor 는 광고 표기 그대로(반지하·중층·고층 등 문자 포함).",
    }
    for r in out_rows:
        report["by_university"][r.get("university", "")] = report["by_university"].get(r.get("university", ""), 0) + 1
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.export:
        exp_dir = REPO_ROOT / "DB" / "export" / "syntitan" / ts
        exp_dir.mkdir(parents=True, exist_ok=True)
        ecols = [c for c in cols if c not in DROP_FOR_EXPORT]
        with (exp_dir / "integrated_listings.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ecols, extrasaction="ignore")
            w.writeheader()
            w.writerows(out_rows)
        report["export"] = str((exp_dir / "integrated_listings.csv").relative_to(REPO_ROOT)).replace("\\", "/")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
