"""정규화 매물(staging) + 도보 경로(routes) → 추천 API 가 읽는 매물 CSV (DB/poc_ewha.csv 와 같은 열).

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.build_candidates <listings.csv> <routes.csv[,routes.csv ...]> <출력이름> [--types 원룸,오피스텔] [--deposit-max 3000]
    예: ... staging/jibsa_listings_v2.csv routes/routes_jibsa_listings_v2_20261108T014958.csv ewha_candidates_v2

- --types / --deposit-max: 대학생 월세 범위로 좁힐 때 쓴다 (반전세처럼 보증금이 큰 매물 제외). 뺀 건수는 report 에 남는다.

- --univ: 대학 (기본 ewha). 그 대학 건물 참조 테이블을 쓴다.
- 강의동 4곳(DB/ref/<대학>_buildings.csv 의 csv_spot_name 이 있는 building_poi) 경로가 **모두 OK** 인 매물만 넣는다.
  경로가 없거나 실패한 매물은 0분·직선거리로 채우지 않고 뺀다 (report 에 사유).
- 좌표는 Kakao 주소 검색(exact, 단일 결과)으로 확정한 지번 좌표다. 건물 출입구가 아니라 지번 대표점이다.
- 관리비 null 은 그대로 둔다 (0 으로 바꾸지 않는다. 추천 API 가 사유와 함께 제외한다).
- poc_ewha.csv 열 뒤에 출처 열(source, listing_status 등)을 붙인다. BE Property 모델은 모르는 열을 무시한다.
- 출력: DB/staging/<출력이름>.csv + .report.json (DB/staging 은 커밋하지 않는다).
"""

from __future__ import annotations

import csv
import json
from collections import Counter

from app.config import REPO_ROOT
from pipeline import universities

DB = REPO_ROOT / "DB"

POC_COLUMNS = [
    "property_id", "university", "property_type", "address", "latitude", "longitude",
    "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon", "area_m2", "source_url", "listing_checked_at",
    "spot1_name", "spot1_walk_min", "spot2_name", "spot2_walk_min", "spot3_name", "spot3_walk_min",
    "spot4_name", "spot4_walk_min", "monthly_housing_cost_manwon",
]
EXTRA_COLUMNS = [
    "source", "source_listing_id", "listing_status", "location_precision", "location_source",
    "duplicate_suspect_group", "route_source", "route_mode", "route_checked_at", "routes_file", "raw_run",
]


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main(listings_rel: str, routes_rel: str, name: str,
         types: set[str] | None = None, deposit_max: float | None = None, univ: str = universities.DEFAULT) -> None:
    spots = [b for b in _read(universities.ref_path(univ))
             if b["point_role"] == "building_poi" and b["csv_spot_name"]]
    listings = _read(DB / listings_rel)
    # routes_rel 은 쉼표로 여러 파일을 줄 수 있다 (날짜별로 나눠 부른 경로 합치기). 뒤 파일이 앞 파일을 대신한다.
    routes: dict[str, dict[str, dict]] = {}
    for rel in routes_rel.split(","):
        for r in _read(DB / rel):
            routes.setdefault(r["property_id"], {})[r["destination_id"]] = r

    out, dropped = [], Counter()
    for p in listings:
        if types and p["property_type"] not in types:
            dropped[f"유형 제외 ({p['property_type']})"] += 1
            continue
        if deposit_max is not None and (p["deposit_manwon"] == "" or float(p["deposit_manwon"]) > deposit_max):
            dropped[f"보증금 {deposit_max:g}만원 초과 또는 미확인"] += 1
            continue
        rs = routes.get(p["property_id"], {})
        legs = [rs.get(s["destination_id"]) for s in spots]
        if not rs:
            dropped["경로 조회 안 함 (주소 확정 불가 또는 캠퍼스 직선 2km 초과)"] += 1
            continue
        if any(leg is None or leg["status"] != "OK" or leg["walk_min"] == "" for leg in legs):
            dropped["강의동 경로 중 실패·누락"] += 1
            continue
        row = {c: p.get(c, "") for c in POC_COLUMNS}
        row["university"] = universities.get(univ)["name"]
        row["property_id"] = universities.candidate_id(univ, p["property_id"])  # 이대 외는 'ku:zigbang:…' 처럼
        row["latitude"], row["longitude"] = legs[0]["start_lat"], legs[0]["start_lng"]
        for i, (s, leg) in enumerate(zip(spots, legs), 1):
            row[f"spot{i}_name"] = s["csv_spot_name"]
            row[f"spot{i}_walk_min"] = leg["walk_min"]
        row.update({
            "source": p["source"], "source_listing_id": p["source_listing_id"],
            "listing_status": p["listing_status"], "location_precision": p["location_precision"],
            "location_source": "kakao_address_exact_single", "duplicate_suspect_group": p["duplicate_suspect_group"],
            "route_source": legs[0]["route_source"], "route_mode": legs[0]["route_mode"],
            "route_checked_at": max(leg["checked_at"] for leg in legs), "routes_file": routes_rel,
            "raw_run": p["raw_run"],
        })
        out.append(row)

    path = DB / "staging" / f"{name}.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=POC_COLUMNS + EXTRA_COLUMNS)
        w.writeheader()
        w.writerows(out)
    report = {
        "listings_in": len(listings),
        "candidates_out": len(out),
        "dropped": dict(dropped),
        "maintenance_null": sum(1 for r in out if r["maintenance_manwon"] in ("", None)),
        "duplicate_suspect_rows": sum(1 for r in out if r["duplicate_suspect_group"]),
        "spots": {f"spot{i}": s["csv_spot_name"] for i, s in enumerate(spots, 1)},
        "inputs": {"listings": listings_rel, "routes": routes_rel},
        "filters": {"types": sorted(types) if types else None, "deposit_max_manwon": deposit_max},
        "university": universities.get(univ)["name"],
        "note": "광고 매물(advertised_unverified). 입주 가능 여부 미확인. 좌표는 지번 대표점, 목적지는 건물 POI(출입구 아님).",
    }
    path.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("listings")
    ap.add_argument("routes")
    ap.add_argument("name")
    ap.add_argument("--types", default="")
    ap.add_argument("--deposit-max", type=float, default=None)
    ap.add_argument("--univ", default=universities.DEFAULT, choices=list(universities.UNIVERSITIES))
    a = ap.parse_args()
    main(a.listings, a.routes, a.name, set(a.types.split(",")) - {""} or None, a.deposit_max, a.univ)
