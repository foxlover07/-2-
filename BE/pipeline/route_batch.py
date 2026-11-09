"""매물 × 캠퍼스 건물 도보 경로 일괄 조회 (Kakao 도보 경로, BROAD_FIRST).

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.route_batch [--input poc_ewha.csv] [--max-calls 400] [--properties P001,P003]
        [--destinations EWHA_ECC,EWHA_POSCO,...] [--max-km 2]

- 매물: --input (DB/ 기준 경로, property_id·address 열 필요). 기본 DB/poc_ewha.csv.
  주소를 Kakao 주소 검색(exact)으로 하나로 확정할 수 있는 매물만 쓴다.
- 같은 주소의 매물은 경로가 같으므로 주소당 한 번만 조회하고 행은 매물마다 쓴다 (raw_file 공유).
- 목적지: --univ 대학의 건물 참조 테이블(DB/ref/ewha_buildings.csv, korea_buildings.csv)의 building_poi
  (출입구 아님, entrance_verified=false). 기본은 ewha.
- 결과: DB/routes/routes_<입력파일이름>_<ts>.csv (긴 형식, CLAUDE.md §11 routes 구조) — 한 건씩 바로 쓴다.
  원본 응답: DB/raw/kakao/<ts>/ (로컬 개발용, 저장 정책 확인 전 커밋/공개 금지).
- 실패(status != OK)는 시간 칸을 비우고 status 를 남긴다. HTTP 에러(429 등)면 즉시 멈춘다.
- 무료 쿼터: 도보 경로 1,000건/일, 주소로 좌표 변환 100,000건/일 (Kakao 쿼터 문서, 2026-11-08 확인). --max-calls 로 상한을 건다.
- --destinations: 목적지를 destination_id 로 좁힌다 (기본: building_poi 전체).
- --max-km: 주소 좌표와 캠퍼스(목적지 평균 좌표)의 직선거리가 이보다 멀면 도보 경로를 부르지 않는다 (status 기록 없이 건너뜀 목록에 남김).
  직선거리는 호출을 줄이는 필터일 뿐이고 도보시간 대신 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import REPO_ROOT, get_settings
from app.tools.kakao import KakaoClient, LatLng, single_exact_match
from pipeline import universities

DB = REPO_ROOT / "DB"
ROUTE_MODE = "BROAD_FIRST"
FIELDS = [
    "property_id", "destination_id", "walk_seconds", "walk_min", "distance_m", "route_source", "route_mode",
    "status", "checked_at", "location_verified", "start_lat", "start_lng", "end_lat", "end_lng",
    "end_point_role", "raw_file",
]


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _haversine_km(a: LatLng, b: LatLng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main(input_rel: str, max_calls: int, only: set[str] | None,
         destinations: set[str] | None = None, max_km: float | None = None, univ: str = universities.DEFAULT) -> None:
    # utf-8-sig: staging CSV 는 BOM 이 붙어 있다 (BOM 없는 파일도 그대로 읽는다)
    props = [r for r in csv.DictReader(open(DB / input_rel, encoding="utf-8-sig"))
             if not only or r["property_id"] in only]
    buildings = [b for b in csv.DictReader(open(universities.ref_path(univ), encoding="utf-8"))
                 if b["point_role"] == "building_poi" and (not destinations or b["destination_id"] in destinations)]
    if destinations and len(buildings) != len(destinations):
        raise SystemExit(f"목적지 중 없는 ID: {destinations - {b['destination_id'] for b in buildings}}")
    campus = LatLng(lat=sum(float(b["latitude"]) for b in buildings) / len(buildings),
                    lng=sum(float(b["longitude"]) for b in buildings) / len(buildings))
    far: list[str] = []
    by_addr: dict[str, list[dict]] = {}
    for p in props:
        by_addr.setdefault(p["address"], []).append(p)
    planned = len(by_addr) * len(buildings)
    print(f"매물 {len(props)}개, 주소 {len(by_addr)}곳, 건물 {len(buildings)}개 → 예정 호출 {planned}건")
    if planned > max_calls:
        raise SystemExit(f"예정 호출 {planned}건 > 상한 {max_calls}건. --max-calls 를 올리거나 매물을 줄인다.")

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_dir = DB / "raw" / "kakao" / ts
    raw_dir.mkdir(parents=True, exist_ok=False)
    out = DB / "routes" / f"routes_{Path(input_rel).stem}_{ts}.csv"
    out.parent.mkdir(exist_ok=True)

    client = KakaoClient(get_settings().kakao_rest_api_key)
    calls = ok = 0
    skipped: list[str] = []
    try:
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for a_idx, (address, group) in enumerate(by_addr.items(), 1):
                addr_key = f"A{a_idx:03d}"
                addr_res = client.search_address(address, exact=True)
                (raw_dir / f"address_{addr_key}.json").write_text(
                    json.dumps({"query": address, "response": addr_res}, ensure_ascii=False), encoding="utf-8")
                match = single_exact_match(addr_res, address)
                ids = [p["property_id"] for p in group]
                if match is None:
                    skipped += ids
                    print(f"{address}: 주소 확정 불가 → 건너뜀 {ids}")
                    continue
                start, _ = match
                if max_km is not None and (km := _haversine_km(start, campus)) > max_km:
                    far += ids
                    print(f"{address}: 캠퍼스 직선 {km:.2f}km > {max_km}km → 도보 경로 생략 {ids}")
                    continue
                for b in buildings:
                    end = LatLng(lat=float(b["latitude"]), lng=float(b["longitude"]))
                    checked_at = _now()
                    r = client.walk(start, end, route_mode=ROUTE_MODE)
                    calls += 1
                    raw_name = f"walk_{addr_key}_{b['destination_id']}.json"
                    (raw_dir / raw_name).write_text(json.dumps(r.raw, ensure_ascii=False), encoding="utf-8")
                    for pid in ids:
                        w.writerow({
                            "property_id": pid, "destination_id": b["destination_id"],
                            "walk_seconds": r.time_s if r.time_s is not None else "",
                            "walk_min": round(r.time_s / 60, 1) if r.time_s is not None else "",
                            "distance_m": r.distance_m if r.distance_m is not None else "",
                            "route_source": "kakao_walk", "route_mode": ROUTE_MODE, "status": r.status,
                            "checked_at": checked_at, "location_verified": "geocode_exact_single",
                            "start_lat": start.lat, "start_lng": start.lng, "end_lat": end.lat, "end_lng": end.lng,
                            "end_point_role": b["point_role"], "raw_file": f"raw/kakao/{ts}/{raw_name}",
                        })
                    f.flush()
                    ok += r.ok
                    if r.status.startswith("HTTP_"):
                        raise SystemExit(f"HTTP 에러 {r.status} — 중단 (쿼터·권한 확인). 지금까지 {calls}건 기록.")
                    time.sleep(0.1)
                print(f"{addr_key} {address} ({len(ids)}개 매물): 완료 (누적 호출 {calls}, OK {ok})", flush=True)
    finally:
        client.close()
    print(f"완료: 호출 {calls}, OK {ok}, 주소 확정 불가 {len(skipped)}건 {skipped}, 직선 {max_km}km 초과 {len(far)}건 → {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="poc_ewha.csv")
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--properties", default="")
    ap.add_argument("--destinations", default="")
    ap.add_argument("--max-km", type=float, default=None)
    ap.add_argument("--univ", default=universities.DEFAULT, choices=list(universities.UNIVERSITIES))
    a = ap.parse_args()
    main(a.input, a.max_calls, set(a.properties.split(",")) - {""} or None,
         set(a.destinations.split(",")) - {""} or None, a.max_km, a.univ)
