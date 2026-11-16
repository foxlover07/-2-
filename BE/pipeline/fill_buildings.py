"""학교 건물 전체 도보시간 채우기 — 빈 (주소 × 건물) 만, 하루 상한 안에서 나눠 부르고 다음 날 이어 간다.

2026-11-16 사용자 결정: 카카오 하루 한도를 나눠 쓰는 것은 인지, **건물 도보시간 전부 반영이 우선**.
순서 이대 → 서강대 → 서울과기대 → 고려대, 하루 960회.

사용 (BE/ 에서, ../home/Scripts/python.exe -X utf8 -m pipeline.fill_buildings ...):
    1) plan  --univ ewha --candidates staging/ewha_candidates_v3_student.csv --walk-table staging/walk_times_v2.csv
       → staging/fill/<대학>_plan_<ts>.csv (주소 · destination_id · 매물 ID 들 · 캠퍼스 직선 km). **API 호출 없음**.
    2) run   --univ ewha --plan staging/fill/<..>.csv --max-calls 960
       → routes/routes_fill_<대학>_<ts>.csv (route_batch 와 같은 긴 형식), 원본 raw/kakao/<ts>/.
         이미 routes_fill_<대학>_*.csv 에 있는 (주소, 건물) 은 다시 부르지 않는다 (실패 포함 → 실패는 report 로 따로 본다).
         HTTP 오류(429 등)면 바로 멈춘다. 다음 날 같은 명령으로 이어 간다.
    3) merge --univ ewha --candidates <..> --walk-table <..>
       → 지금까지의 routes_fill_<대학>_*.csv OK 결과를 도보시간 표에 (매물, 건물) 이 없는 행만 덧붙인다.
         덧붙이기 전 표는 staging/fill/ 에 복사해 둔다.

규칙 (route_batch 와 같음): 출발점은 지번 주소를 카카오 주소 검색(exact)으로 하나로 확정한 좌표, 도착점은 건물 POI 대표 지점
(출입구 아님). 실패는 시간을 비우고 status 를 남긴다 — 0분·직선거리로 채우지 않는다. 캠퍼스 직선 max_km 밖 주소는 부르지 않는다
(직선거리는 호출을 줄이는 필터일 뿐 도보시간 대신 쓰지 않는다).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import REPO_ROOT
from pipeline import build_walk_table, universities
from pipeline.refill_listings import _append, _read, routes_by_address

DB = REPO_ROOT / "DB"
FILL_DIR = DB / "staging" / "fill"


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _buildings(univ: str) -> list[dict]:
    _, ref = _read(universities.ref_path(univ))
    return [b for b in ref if b["point_role"] == "building_poi"]


def _fill_routes(univ: str) -> list[Path]:
    return sorted((DB / "routes").glob(f"routes_fill_{univ}_*.csv"))


def _attempted(univ: str, addr_of: dict[str, str]) -> set[tuple[str, str]]:
    """이미 이 단계에서 부른 (주소, 건물) — 성공·실패 모두 (같은 날·다음 날 다시 부르지 않게)."""
    out = set()
    for p in _fill_routes(univ):
        _, rows = _read(p)
        for r in rows:
            a = addr_of.get(r["property_id"])
            if a:
                out.add((a, r["destination_id"]))
    return out


def plan(univ: str, candidates_rel: str, walk_rel: str, max_km: float) -> None:
    _, cands = _read(DB / candidates_rel)
    _, walk = _read(DB / walk_rel)
    have_pid = {(r["property_id"], r["destination_id"]) for r in walk if r.get("walk_min")}
    by_addr_ok, _ = routes_by_address()  # 같은 지번 주소로 예전에 부른 OK 경로는 다시 부르지 않는다
    blds = _buildings(univ)
    campus = (sum(float(b["latitude"]) for b in blds) / len(blds), sum(float(b["longitude"]) for b in blds) / len(blds))

    groups: dict[str, list[dict]] = {}
    for p in cands:
        groups.setdefault(p["address"], []).append(p)
    rows, cnt = [], Counter()
    for addr, ps in groups.items():
        try:
            km = _km(float(ps[0]["latitude"]), float(ps[0]["longitude"]), *campus)
        except (KeyError, TypeError, ValueError):
            cnt["좌표 없음(주소)"] += 1
            continue
        for b in blds:
            d = b["destination_id"]
            if d in by_addr_ok.get(addr, {}) or all((p["property_id"], d) in have_pid for p in ps):
                cnt["이미 있음"] += 1
                continue
            if km > max_km:
                cnt[f"캠퍼스 직선 {max_km:g}km 초과"] += 1
                continue
            rows.append({"address": addr, "destination_id": d,
                         "property_ids": "|".join(p["property_id"] for p in ps), "campus_km": round(km, 3)})
    rows.sort(key=lambda r: (r["campus_km"], r["address"], r["destination_id"]))  # 가까운 주소부터
    FILL_DIR.mkdir(parents=True, exist_ok=True)
    out = FILL_DIR / f"{univ}_plan_{datetime.now():%Y%m%dT%H%M%S}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["address", "destination_id", "property_ids", "campus_km"])
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"university": universities.get(univ)["name"], "buildings": len(blds), "addresses": len(groups),
                      "calls_needed": len(rows), "skipped": dict(cnt), "days_at_960": math.ceil(len(rows) / 960),
                      "plan": str(out.relative_to(DB)).replace("\\", "/")}, ensure_ascii=False))


def run(univ: str, plan_rel: str, max_calls: int) -> None:
    from app.config import get_settings  # noqa: PLC0415
    from app.tools.kakao import KakaoClient, LatLng, single_exact_match  # noqa: PLC0415
    from pipeline.route_batch import FIELDS, ROUTE_MODE, _now  # noqa: PLC0415

    _, prow = _read(DB / plan_rel)
    addr_of = {pid: r["address"] for r in prow for pid in r["property_ids"].split("|")}
    done = _attempted(univ, addr_of)
    todo = [r for r in prow if (r["address"], r["destination_id"]) not in done]
    blds = {b["destination_id"]: b for b in _buildings(univ)}
    print(f"계획 {len(prow)}건 중 이미 부름 {len(prow) - len(todo)}건, 남음 {len(todo)}건, 오늘 상한 {max_calls}건", flush=True)
    if not todo:
        return

    base = datetime.now().strftime("%Y%m%dT%H%M%S")
    ts, n = base, 1
    while (DB / "raw" / "kakao" / ts).exists() or (DB / "routes" / f"routes_fill_{univ}_{ts}.csv").exists():
        n += 1
        ts = f"{base}_{n}"  # 같은 초에 다시 실행해도 덮어쓰지 않게
    raw_dir = DB / "raw" / "kakao" / ts
    raw_dir.mkdir(parents=True, exist_ok=False)
    out = DB / "routes" / f"routes_fill_{univ}_{ts}.csv"
    client = KakaoClient(get_settings().kakao_rest_api_key)
    calls = ok = 0
    starts: dict[str, LatLng | None] = {}
    cnt = Counter()
    stop = ""
    try:
        with out.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in todo:
                if calls >= max_calls:
                    stop = "오늘 상한"
                    break
                addr, dest = r["address"], r["destination_id"]
                if addr not in starts:
                    res = client.search_address(addr, exact=True)
                    m = single_exact_match(res, addr)
                    starts[addr] = m[0] if m else None
                    if m is None:
                        (raw_dir / f"address_unresolved_{len(starts):04d}.json").write_text(
                            json.dumps({"query": addr, "response": res}, ensure_ascii=False), encoding="utf-8")
                start = starts[addr]
                if start is None:
                    cnt["주소 확정 불가"] += 1
                    continue
                b = blds[dest]
                end = LatLng(lat=float(b["latitude"]), lng=float(b["longitude"]))
                checked_at = _now()
                res = client.walk(start, end, route_mode=ROUTE_MODE)
                calls += 1
                raw_name = f"walk_{calls:04d}_{dest}.json"
                (raw_dir / raw_name).write_text(json.dumps(res.raw, ensure_ascii=False), encoding="utf-8")
                for pid in r["property_ids"].split("|"):
                    w.writerow({
                        "property_id": pid, "destination_id": dest,
                        "walk_seconds": res.time_s if res.time_s is not None else "",
                        "walk_min": round(res.time_s / 60, 1) if res.time_s is not None else "",
                        "distance_m": res.distance_m if res.distance_m is not None else "",
                        "route_source": "kakao_walk", "route_mode": ROUTE_MODE, "status": res.status,
                        "checked_at": checked_at, "location_verified": "geocode_exact_single",
                        "start_lat": start.lat, "start_lng": start.lng, "end_lat": end.lat, "end_lng": end.lng,
                        "end_point_role": b["point_role"], "raw_file": f"raw/kakao/{ts}/{raw_name}",
                    })
                f.flush()
                ok += res.ok
                cnt[res.status] += 1
                if res.status.startswith("HTTP_"):
                    stop = f"HTTP 오류 {res.status} (쿼터·권한 확인)"
                    break
                if calls % 100 == 0:
                    print(f"  호출 {calls}, OK {ok}", flush=True)
                time.sleep(0.1)
    finally:
        client.close()
    left = len(todo) - calls - cnt["주소 확정 불가"]
    print(json.dumps({"university": universities.get(univ)["name"], "calls": calls, "ok": ok, "status": dict(cnt),
                      "stopped": stop or "계획 끝", "left": max(left, 0), "routes": str(out.relative_to(DB)).replace("\\", "/")},
                     ensure_ascii=False))


def merge(univ: str, candidates_rel: str, walk_rel: str) -> None:
    routes = [str(p.relative_to(DB)).replace("\\", "/") for p in _fill_routes(univ)]
    if not routes:
        raise SystemExit("routes_fill 결과가 없습니다.")
    FILL_DIR.mkdir(parents=True, exist_ok=True)
    name = f"fill/{univ}_walk_{datetime.now():%Y%m%dT%H%M%S}"
    build_walk_table.main(candidates_rel, name, routes)
    _, existing = _read(DB / walk_rel)
    have = {(r["property_id"], r["destination_id"]) for r in existing}
    _, built = _read(DB / f"staging/{name}.csv")
    add = [r for r in built if (r["property_id"], r["destination_id"]) not in have]
    tmp = DB / "staging" / f"{name}_add.csv"
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(built[0]) if built else build_walk_table.FIELDS)
        w.writeheader()
        w.writerows(add)
    n = _append(walk_rel, f"staging/{name}_add.csv", "", FILL_DIR)
    print(json.dumps({"university": universities.get(univ)["name"], "walk_rows_added": n, "walk_table": walk_rel,
                      "routes_files": len(routes)}, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["plan", "run", "merge"])
    ap.add_argument("--univ", required=True, choices=list(universities.UNIVERSITIES))
    ap.add_argument("--candidates")
    ap.add_argument("--walk-table")
    ap.add_argument("--plan")
    ap.add_argument("--max-calls", type=int, default=960)
    ap.add_argument("--max-km", type=float, default=2.0)
    a = ap.parse_args()
    if a.step == "plan":
        plan(a.univ, a.candidates, a.walk_table, a.max_km)
    elif a.step == "run":
        run(a.univ, a.plan, a.max_calls)
    else:
        merge(a.univ, a.candidates, a.walk_table)


if __name__ == "__main__":
    main()
