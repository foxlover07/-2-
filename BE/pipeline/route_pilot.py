"""Kakao 도보 경로 소량 검증 (D 단계): 매물 1개 × 강의동 4개 × route_mode.

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.route_pilot P003 BROAD_FIRST SHORTEST

- 매물 좌표: DB/poc_ewha.csv 주소를 Kakao 주소 검색(exact)으로 확정할 수 있을 때만 사용.
- 목적지: DB/ref/ewha_buildings.csv 의 building_poi (출입구 아님, entrance_verified=false).
- 결과: DB/routes/route_pilot_<ts>.csv (수동 측정값과 비교), 원본 응답은 DB/raw/kakao/<ts>/ (로컬 개발용).
- 실패(status != OK)는 시간 칸을 비우고 status 를 남긴다. 0분·직선거리로 대체하지 않는다.
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone

from app.config import REPO_ROOT, get_settings
from app.tools.kakao import KakaoClient, LatLng, single_exact_match

DB = REPO_ROOT / "DB"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main(property_id: str, modes: list[str]) -> None:
    prop = next(r for r in csv.DictReader(open(DB / "poc_ewha.csv", encoding="utf-8")) if r["property_id"] == property_id)
    buildings = [b for b in csv.DictReader(open(DB / "ref" / "ewha_buildings.csv", encoding="utf-8"))
                 if b["point_role"] == "building_poi"]
    manual = {prop[f"spot{i}_name"]: prop[f"spot{i}_walk_min"] for i in range(1, 5)}

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_dir = DB / "raw" / "kakao" / ts
    raw_dir.mkdir(parents=True, exist_ok=False)
    client = KakaoClient(get_settings().kakao_rest_api_key)
    try:
        addr_res = client.search_address(prop["address"], exact=True)
        (raw_dir / f"address_{property_id}.json").write_text(json.dumps(addr_res, ensure_ascii=False), encoding="utf-8")
        match = single_exact_match(addr_res, prop["address"])
        if match is None:
            raise SystemExit(f"{property_id} 주소를 하나로 확정할 수 없다: {prop['address']}")
        start, doc = match
        print(f"{property_id} {prop['address']} -> {doc['address_name']} ({start.lat}, {start.lng})")

        rows = []
        for b in buildings:
            end = LatLng(lat=float(b["latitude"]), lng=float(b["longitude"]))
            for mode in modes:
                checked_at = _now()
                r = client.walk(start, end, route_mode=mode)
                raw_name = f"walk_{property_id}_{b['destination_id']}_{mode}.json"
                (raw_dir / raw_name).write_text(json.dumps(r.raw, ensure_ascii=False), encoding="utf-8")
                manual_min = manual.get(b["csv_spot_name"]) or None
                api_min = round(r.time_s / 60, 1) if r.time_s is not None else None
                rows.append({
                    "property_id": property_id,
                    "destination_id": b["destination_id"],
                    "csv_spot_name": b["csv_spot_name"],
                    "route_source": "kakao_walk",
                    "route_mode": mode,
                    "status": r.status,
                    "walk_seconds": r.time_s,
                    "walk_min": api_min,
                    "distance_m": r.distance_m,
                    "manual_walk_min": manual_min,
                    "diff_min_api_minus_manual": round(api_min - float(manual_min), 1) if api_min is not None and manual_min else None,
                    "start_lat": start.lat, "start_lng": start.lng,
                    "end_lat": end.lat, "end_lng": end.lng,
                    "end_point_role": b["point_role"],
                    "location_verified": "geocode_exact_single",
                    "checked_at": checked_at,
                    "raw_file": f"raw/kakao/{ts}/{raw_name}",
                })
                print(f"  {b['csv_spot_name']:8s} {mode:12s} {r.status:22s} api={api_min} min {r.distance_m} m  manual={manual_min}")
    finally:
        client.close()

    out_dir = DB / "routes"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"route_pilot_{ts}.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: ("" if v is None else v) for k, v in row.items()} for row in rows)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] or ["BROAD_FIRST"])
