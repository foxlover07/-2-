"""routes 긴 형식 → 매물 × 건물 도보시간(분) 넓은 표.

사용 (BE/ 에서):
    python -m pipeline.routes_wide routes_poc_ewha_20261106T210522 poc_ewha_walk_kakao_v1

- status != OK 인 칸은 비운다 (0 으로 채우지 않는다).
- 열 이름: walk_min__<destination_id>. 순서는 DB/ref/ewha_buildings.csv 를 따른다.
- 원본 poc_ewha.csv 는 바꾸지 않는다.
"""

from __future__ import annotations

import csv
import sys

from app.config import REPO_ROOT

DB = REPO_ROOT / "DB"


def build(routes_name: str) -> list[dict]:
    with open(DB / "routes" / f"{routes_name}.csv", encoding="utf-8") as f:
        routes = list(csv.DictReader(f))
    with open(DB / "ref" / "ewha_buildings.csv", encoding="utf-8") as f:
        ref_order = [r["destination_id"] for r in csv.DictReader(f)]
    present = {r["destination_id"] for r in routes}
    order = [d for d in ref_order if d in present]

    table: dict[str, dict] = {}
    for r in routes:
        row = table.setdefault(r["property_id"], {"property_id": r["property_id"], "routes_file": f"routes/{routes_name}.csv"})
        row[f"walk_min__{r['destination_id']}"] = r["walk_min"] if r["status"] == "OK" else ""
    columns = ["property_id", "routes_file", *(f"walk_min__{d}" for d in order)]
    return [{c: row.get(c, "") for c in columns} for row in sorted(table.values(), key=lambda x: x["property_id"])]


def write(rows: list[dict], out_name: str):
    out = DB / "staging" / f"{out_name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return out


if __name__ == "__main__":
    rows = build(sys.argv[1])
    print(write(rows, sys.argv[2]), len(rows), "rows")
