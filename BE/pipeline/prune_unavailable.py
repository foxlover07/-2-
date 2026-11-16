"""거래 끝난 매물(재확인 결과 unavailable)을 매물 CSV 에서 지운다 (파일을 그 자리에서 고친다).

사용 (BE/ 에서, check_listing_status 다음에):
    ../home/Scripts/python.exe -X utf8 -m pipeline.prune_unavailable [--dry-run]

- 입력: DATA_CSV_PATH 의 매물 CSV 들·WALK_TABLE_PATH 의 도보시간 표 + DB/staging/listing_status_*.csv (매물마다 가장 최근 판정, app.listing_status).
- 2026-11-16 사용자 결정: 새 버전을 만들지 않고 원래 파일에서 행을 지운다.
  지운 행은 원래 열 그대로 DB/staging/pruned/<파일 이름>_removed_<ts>.csv 에 모아 둔다 (되살릴 때 쓴다)
  + 같은 이름 .report.json (원 사이트 상태·확인 시각).
- 남은 행 중 재확인에서 거래 가능으로 나온 매물은 listing_status 열(있으면)을 active 로 바꾼다.
- 확인하지 못한 매물(error·미확인)은 지우지 않는다 (판정 보류).
- 끝나면 pipeline.build_integrated 로 통합 데이터셋을 다시 만든다 (자동 실행 안 함).
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime

from app.data import _csv_paths, _walk_table_paths
from app.listing_status import load_status


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="지울 건수만 보고 파일은 고치지 않는다")
    a = ap.parse_args()
    status = load_status()
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    summary = {}
    for path in _csv_paths():
        with path.open(encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            cols = list(rd.fieldnames or [])
            rows = list(rd)
        keep, removed, meta, unchecked = [], [], [], 0
        for r in rows:
            st = status.get((r.get("source", ""), r.get("source_listing_id", "")))
            if st and st.status == "unavailable":
                removed.append(r)
                meta.append({"property_id": r.get("property_id"), "source_listing_id": r.get("source_listing_id"),
                             "upstream_status": st.upstream_status, "checked_at": st.checked_at})
                continue
            if st and st.status == "active":
                if "listing_status" in cols:
                    r = {**r, "listing_status": "active"}
            else:
                unchecked += 1
            keep.append(r)
        summary[path.name] = {"before": len(rows), "kept": len(keep), "removed": len(removed),
                              "unchecked_kept": unchecked}
        if a.dry_run or not removed:
            continue
        pruned_dir = path.parent / "pruned"
        pruned_dir.mkdir(parents=True, exist_ok=True)
        back = pruned_dir / f"{path.stem}_removed_{ts}.csv"
        with back.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(removed)
        back.with_suffix(".report.json").write_text(
            json.dumps({"source_file": path.name, **summary[path.name], "removed_rows": meta},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(keep)
        tmp.replace(path)
    # 도보시간 표(WALK_TABLE_PATH)에서도 거래 불가 매물의 행을 지운다. property_id 는 'zigbang:123' 또는 'ku:zigbang:123'.
    for path in _walk_table_paths():
        with path.open(encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            cols = list(rd.fieldnames or [])
            rows = list(rd)

        def gone(pid: str) -> bool:
            parts = pid.split(":")
            st = status.get((parts[-2], parts[-1])) if len(parts) >= 2 else None
            return bool(st and st.status == "unavailable")

        removed = [r for r in rows if gone(r["property_id"])]
        summary[path.name] = {"before": len(rows), "kept": len(rows) - len(removed), "removed": len(removed)}
        if a.dry_run or not removed:
            continue
        pruned_dir = path.parent / "pruned"
        pruned_dir.mkdir(parents=True, exist_ok=True)
        for name, part in ((pruned_dir / f"{path.stem}_removed_{ts}.csv", removed),
                           (path.with_suffix(".csv.tmp"), [r for r in rows if not gone(r["property_id"])])):
            with name.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(part)
        path.with_suffix(".csv.tmp").replace(path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not a.dry_run:
        print("지운 행은 각 파일 폴더의 pruned/ 에 보관했습니다.")


if __name__ == "__main__":
    main()
