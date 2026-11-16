"""매물 × 강의동 도보시간 표 (긴 형식) — 추천 API 가 강의동 4곳보다 많은 건물을 쓰게 한다.

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.build_walk_table <매물 CSV> <출력이름> <routes CSV> [routes CSV ...]
    예: ... staging/ewha_candidates_v2_student.csv walk_times_v1 \\
            routes/routes_ewha_candidates_v2_student_20261108T031428.csv \\
            routes/routes_jibsa_route_sample_v1_20261106T210933.csv

- 매물 CSV 의 spot1~4 도보시간(기존 4개 강의동)을 그대로 넣고, routes CSV 의 OK 결과를 더한다.
- routes 의 property_id 가 매물 CSV 에 없으면 **같은 지번 주소**의 매물에 붙인다
  (11/06 표본 경로처럼 예전 매물 ID 로 계산한 경로 재사용. 출발점이 같은 주소 좌표라 경로가 같다).
  주소는 staging/jibsa_listings_*.csv 들에서 찾는다.
- 같은 (매물, 건물)이 여러 번 있으면 가장 최근 checked_at 을 쓴다. 실패(status != OK)는 넣지 않는다 (0분·직선거리로 채우지 않음).
- 건물 이름: ref 의 csv_spot_name, 없으면 Kakao 장소 이름에서 대학 이름(과 '서울캠퍼스')을 뗀 것.
  모든 대학(pipeline/universities.py)의 ref 를 읽으므로 이대·고려대 매물을 한 표에 같이 둘 수 있다.
- 출력: DB/staging/<출력이름>.csv (커밋하지 않는다). BE 는 WALK_TABLE_PATH 로 읽는다.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter

from app.config import REPO_ROOT
from pipeline import universities

DB = REPO_ROOT / "DB"
FIELDS = ["property_id", "building", "destination_id", "walk_min", "route_source", "route_mode", "checked_at", "source_file"]


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def building_names() -> dict[str, str]:
    """모든 대학의 건물 참조 테이블 → destination_id: 건물 이름 (destination_id 는 대학마다 접두어가 달라 겹치지 않는다)."""
    names = {}
    for u in universities.UNIVERSITIES:
        path = universities.ref_path(u)
        if not path.exists():
            continue
        prefix = universities.get(u)["name"]
        for b in _read(path):
            if b["point_role"] != "building_poi":
                continue
            name = b["csv_spot_name"] or (
                b["kakao_place_name"].removeprefix(prefix).strip().removeprefix("서울캠퍼스").strip())
            names[b["destination_id"]] = name
    return names


def main(listings_rel: str, name: str, routes_rels: list[str]) -> None:
    names = building_names()
    spot_to_dest = {v: k for k, v in names.items()}
    listings = _read(DB / listings_rel)
    ids = {p["property_id"] for p in listings}
    by_address: dict[str, list[str]] = {}
    for p in listings:
        by_address.setdefault(p["address"], []).append(p["property_id"])
    # 예전 매물 ID → 주소 (11/06 표본 등)
    old_address: dict[str, str] = {}
    for f in sorted((DB / "staging").glob("jibsa_*.csv")):
        for r in _read(f):
            if r.get("property_id") and r.get("address"):
                old_address.setdefault(r["property_id"], r["address"])

    table: dict[tuple[str, str], dict] = {}

    def put(row: dict) -> None:
        key = (row["property_id"], row["destination_id"])
        if key not in table or row["checked_at"] > table[key]["checked_at"]:
            table[key] = row

    # 1) 매물 CSV 의 기존 4개 강의동
    for p in listings:
        for i in range(1, 5):
            spot, walk = p.get(f"spot{i}_name"), p.get(f"spot{i}_walk_min")
            if spot and walk:
                put({"property_id": p["property_id"], "building": spot, "destination_id": spot_to_dest.get(spot, ""),
                     "walk_min": walk, "route_source": p.get("route_source", ""), "route_mode": p.get("route_mode", ""),
                     "checked_at": p.get("route_checked_at", ""), "source_file": listings_rel})

    # 2) routes CSV
    stats = Counter()
    for rel in routes_rels:
        for r in _read(DB / rel):
            if r["status"] != "OK" or r["walk_min"] == "" or r["destination_id"] not in names:
                stats["skip_not_ok_or_unknown"] += 1
                continue
            # 경로는 원래 매물 ID 로 계산했으므로, 후보 CSV 에서 대학 접두어가 붙은 ID('ku:zigbang:…')도 찾는다
            prefixed = [universities.candidate_id(u, r["property_id"]) for u in universities.UNIVERSITIES]
            direct = [pid for pid in dict.fromkeys([r["property_id"], *prefixed]) if pid in ids]
            targets = direct or by_address.get(old_address.get(r["property_id"], ""), [])
            if not targets:
                stats["skip_no_listing"] += 1
                continue
            for pid in targets:
                put({"property_id": pid, "building": names[r["destination_id"]], "destination_id": r["destination_id"],
                     "walk_min": r["walk_min"], "route_source": r["route_source"], "route_mode": r["route_mode"],
                     "checked_at": r["checked_at"], "source_file": rel})
                stats["used" if direct else "used_by_address"] += 1

    rows = sorted(table.values(), key=lambda x: (x["property_id"], x["destination_id"]))
    out = DB / "staging" / f"{name}.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    coverage = Counter(r["building"] for r in rows)
    report = {"listings": len(listings), "rows": len(rows), "route_rows": dict(stats),
              "coverage_by_building": dict(sorted(coverage.items(), key=lambda kv: -kv[1]))}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
