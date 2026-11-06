"""jibsa 원본 JSON → 공통 매물 스키마 (CLAUDE.md §11).

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.jibsa_adapter <출력이름> <run 폴더> [<run 폴더> ...]
    예) ... jibsa_adapter jibsa_listings_v1 20261106T205458
뒤에 준 run 폴더의 같은 조회(key)가 앞의 것을 대신한다 (재조회 반영).

원칙
- 원본에 없는 값은 만들지 않는다. jibsa 는 매물 좌표를 주지 않으므로 latitude/longitude 는 비운다.
- manage_cost_manwon 이 null 이면 maintenance_manwon 도 null, 실질 주거비도 null (0 으로 처리 금지).
- 주소 정확도: 번지까지 있으면 'lot', 동까지만 있으면 'dong'. 지오코딩 전이므로 모두 location_unverified.
- 중복은 합치지 않고 '의심' 표시만 한다 (보증금·월세·면적·층이 모두 같을 때).
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from app.config import REPO_ROOT

RAW_ROOT = REPO_ROOT / "DB" / "raw" / "jibsa"
STAGING_DIR = REPO_ROOT / "DB" / "staging"
UNIVERSITY = "이화여자대학교"

COLUMNS = [
    # 공통 후보 필드 (CLAUDE.md §11)
    "property_id", "source", "source_listing_id", "source_url", "university", "property_type",
    "address", "location_precision", "latitude", "longitude",
    "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon", "area_m2",
    "listing_checked_at", "listing_status",
    # 파생 / 보조
    "monthly_housing_cost_manwon", "location_unverified", "trade_type", "room_type", "floor",
    "building_floor", "title", "registered_at", "approved_at", "complex_name", "in_regions",
    "short_lease", "duplicate_suspect_group", "found_in", "raw_run", "raw_file",
]

# 번지(예: '대현동 53-33', '성산로 573-74')로 끝나면 번지 단위 주소로 본다.
LOT_RE = re.compile(r"\s\d+(-\d+)?$")


def location_precision(address: str | None) -> str | None:
    if not address:
        return None
    return "lot" if LOT_RE.search(address.strip()) else "dong"


def housing_cost(rent, maint):
    if rent is None or maint is None:
        return None
    return round(rent + maint, 2)


def successful_runs(run_dirs: list[Path]) -> list[tuple[Path, dict]]:
    """주어진 run 폴더들에서 성공(exit 0) 조회를 고른다. 같은 key 면 뒤의 폴더가 이긴다.

    key 가 없는 옛 manifest(v0)는 source 를 key 로 쓴다.
    """
    picked: dict[str, tuple[Path, dict]] = {}
    for run_dir in run_dirs:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        for run in manifest["runs"]:
            if run["exit_code"] == 0:
                picked[run.get("key", run["source"])] = (run_dir, run)
    return list(picked.values())


def normalize_listing(item: dict, run_dir: Path, run: dict) -> dict:
    source = item["source"]
    rent = item.get("rent_manwon")
    maint = item.get("manage_cost_manwon")
    address = item.get("address")
    return {
        "property_id": f"{source}:{item['id']}",
        "source": source,
        "source_listing_id": item["id"],
        "source_url": item.get("url"),
        "university": UNIVERSITY,
        "property_type": item.get("property_type"),
        "address": address,
        "location_precision": location_precision(address),
        "latitude": None,
        "longitude": None,
        "deposit_manwon": item.get("deposit_manwon"),
        "monthly_rent_manwon": rent,
        "maintenance_manwon": maint,
        "area_m2": item.get("area_m2"),
        "listing_checked_at": run["finished_at"],
        "listing_status": "advertised_unverified",
        "monthly_housing_cost_manwon": housing_cost(rent, maint),
        "location_unverified": True,
        "trade_type": item.get("trade_type"),
        "room_type": item.get("room_type"),
        "floor": item.get("floor"),
        "building_floor": item.get("building_floor"),
        "title": item.get("title"),
        "registered_at": item.get("registered_at"),
        "approved_at": item.get("approved_at"),
        "complex_name": item.get("complex_name"),
        "in_regions": item.get("in_regions"),
        "short_lease": item.get("short_lease"),
        "duplicate_suspect_group": None,
        "found_in": run.get("key", source),
        "raw_run": run_dir.name,
        "raw_file": run["stdout_file"],
    }


def mark_duplicates(rows: list[dict]) -> None:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["deposit_manwon"], r["monthly_rent_manwon"], r["area_m2"], r["floor"])
        if None not in key:
            groups[key].append(r)
    n = 0
    for members in groups.values():
        if len(members) > 1:
            n += 1
            for r in members:
                r["duplicate_suspect_group"] = f"D{n:02d}"


def build(run_dirs: list[Path]) -> list[dict]:
    """같은 매물(source+id)이 여러 조회에 나오면 처음 것 하나만 두고 found_in 에 조회 key 를 모은다."""
    rows: dict[str, dict] = {}
    for run_dir, run in successful_runs(run_dirs):
        data = json.loads((run_dir / run["stdout_file"]).read_text(encoding="utf-8"))
        for item in data.get("listings", []):
            row = normalize_listing(item, run_dir, run)
            if row["property_id"] in rows:
                rows[row["property_id"]]["found_in"] += "|" + row["found_in"]
            else:
                rows[row["property_id"]] = row
    out = list(rows.values())
    mark_duplicates(out)
    return out


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def quality_report(rows: list[dict]) -> dict:
    n = len(rows)
    by_source: dict[str, int] = defaultdict(int)
    for r in rows:
        by_source[r["source"]] += 1
    missing = {c: sum(r[c] is None for r in rows) for c in COLUMNS}
    precision: dict[str, int] = defaultdict(int)
    for r in rows:
        precision[str(r["location_precision"])] += 1
    return {
        "total": n,
        "by_source": dict(by_source),
        "missing_count": {c: m for c, m in missing.items() if m},
        "location_precision": dict(precision),
        "out_of_region": sum(r["in_regions"] is False for r in rows),
        "duplicate_suspect_groups": sorted({r["duplicate_suspect_group"] for r in rows} - {None}),
        "routable_now": sum(r["latitude"] is not None for r in rows),
    }


def write(rows: list[dict], out_dir: Path = STAGING_DIR, name: str = "jibsa_listings_v0") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{name}.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r[c]) for c in COLUMNS})
    report = quality_report(rows)
    (out_dir / f"{name}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        raise SystemExit("사용: python -m pipeline.jibsa_adapter <출력이름> <run 폴더> [...]")
    rows = build([RAW_ROOT / name for name in sys.argv[2:]])
    path = write(rows, name=sys.argv[1])
    print(path)
    print(json.dumps(quality_report(rows), ensure_ascii=False, indent=2))
