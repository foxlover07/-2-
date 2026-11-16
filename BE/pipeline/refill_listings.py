"""거래 끝난 매물을 지운 뒤 새 매물로 다시 채운다 — 이미 있는 매물·겹치는 매물은 빼고, 이미 부른 경로는 다시 부르지 않는다.

사용 (BE/ 에서):
    1) 준비:  ../home/Scripts/python.exe -X utf8 -m pipeline.refill_listings prepare --univ korea \\
                 --listings staging/ku_listings_v2.csv --candidates staging/ku_candidates_v1_student.csv
       → staging/refill/<대학>_<ts>_new.csv        (새로 넣을 매물, 어댑터 열 그대로)
         routes/routes_reuse_<대학>_<ts>.csv        (같은 지번 주소로 이미 부른 경로를 새 매물 ID 로 옮겨 적은 것)
         staging/refill/<대학>_<ts>_needroute.csv  (경로를 새로 불러야 하는 매물 → route_batch --input)
         staging/refill/<대학>_<ts>.report.json
    2) 경로:  route_batch --input staging/refill/<..>_needroute.csv --univ korea --destinations <기본 4곳> --max-km 2
    3) 합치기: ... refill_listings merge --univ korea --new staging/refill/<..>_new.csv \\
                 --routes routes/routes_reuse_<..>.csv,routes/routes_<..>_needroute_<ts>.csv \\
                 --candidates staging/ku_candidates_v1_student.csv --walk-table staging/ku_walk_times_v1_student.csv
       → build_candidates 로 새 매물 후보를 만들고 **기존 후보 CSV·도보시간 표에 행을 덧붙인다** (2026-11-16 사용자 결정:
         새 버전 대신 원래 파일을 고친다). 덧붙이기 전 파일은 staging/refill/ 에 복사해 둔다.

빼는 매물 ('겹치는 것', report 에 건수):
- 이미 후보에 있는 매물 (source + source_listing_id 같음)
- 같은 매물로 보이는 것: 지번 주소·보증금·월세·면적이 같고 층도 같거나 한쪽이 미확인 (둘 다 알려져 있고 다르면 다른 방) (기존 후보 또는 이번에 먼저 나온 새 매물과)
- 거래 가능 재확인에서 거래 불가로 나온 매물 ID
- 대학생 월세 범위 밖: 유형 원룸·오피스텔 외, 보증금 3,000만 원 초과·미확인 (기존 후보와 같은 기준)

경로 재사용: 경로는 지번 주소 좌표에서 출발하므로 같은 주소면 같다 (route_batch 도 주소당 한 번만 부른다).
DB/routes 의 OK 결과를 주소로 모아, 기본 강의동이 모두 있는 주소는 경로를 다시 부르지 않는다. 실패를 0분·직선거리로 채우지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from collections import Counter
from datetime import datetime

from app.config import REPO_ROOT
from app.listing_status import load_status
from pipeline import build_candidates, build_walk_table, universities

DB = REPO_ROOT / "DB"
TYPES = {"원룸", "오피스텔"}
DEPOSIT_MAX = 3000.0


def _read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames or []), list(rd)


def _num(v: str) -> str:
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return ""


def same_key(r: dict) -> tuple:
    """같은 매물로 보는 기준: 지번 주소·보증금·월세·면적 (+ 층: is_same 에서 따로 본다)."""
    return (r.get("address", "").strip(), _num(r.get("deposit_manwon")), _num(r.get("monthly_rent_manwon")),
            _num(r.get("area_m2")))


def is_same(floors_seen: set[str], floor: str) -> bool:
    """주소·가격·면적이 같은 매물이 이미 있을 때, 층이 둘 다 알려져 있고 다르면 다른 방, 아니면(같거나 한쪽 미확인) 같은 매물."""
    if not floors_seen:
        return False
    return not floor or "" in floors_seen or floor in floors_seen


def base_destinations(univ: str) -> list[str]:
    _, ref = _read(universities.ref_path(univ))
    return [b["destination_id"] for b in ref if b["point_role"] == "building_poi" and b["csv_spot_name"]]


def _address_of() -> dict[str, str]:
    """매물 ID(접두어 있든 없든) → 지번 주소. staging 의 매물·후보 CSV 전부에서."""
    out: dict[str, str] = {}
    for p in sorted((DB / "staging").glob("*.csv")):
        if not ("listings" in p.name or "candidates" in p.name):
            continue
        _, rows = _read(p)
        for r in rows:
            if r.get("property_id") and r.get("address"):
                out.setdefault(r["property_id"], r["address"])
                raw = r["property_id"].split(":", 1)[1] if r["property_id"].count(":") >= 2 else None
                if raw:
                    out.setdefault(raw, r["address"])
    return out


def routes_by_address() -> tuple[dict[str, dict[str, dict]], list[str]]:
    """지번 주소 → {destination_id: 가장 최근 OK 경로 행}."""
    addr = _address_of()
    out: dict[str, dict[str, dict]] = {}
    fields: list[str] = []
    for p in sorted((DB / "routes").glob("routes_*.csv")):
        cols, rows = _read(p)
        fields = fields or cols
        for r in rows:
            a = addr.get(r["property_id"])
            if not a or r.get("status") != "OK" or not r.get("walk_min"):
                continue
            cur = out.setdefault(a, {}).get(r["destination_id"])
            if cur is None or r["checked_at"] > cur["checked_at"]:
                out[a][r["destination_id"]] = r
    return out, fields


def prepare(univ: str, listings_rel: str, candidates_rel: str) -> None:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    cols, new_rows = _read(DB / listings_rel)
    _, cands = _read(DB / candidates_rel)
    # 기존 후보의 층은 매물 CSV 에서 가져온다 (후보 CSV 에는 층 열이 없다)
    floor_of: dict[tuple, str] = {}
    for p in (DB / "staging").glob("*listings*.csv"):
        _, rows = _read(p)
        for r in rows:
            if (r.get("floor") or "").strip():   # 층이 비어 있는 옛 파일보다 층이 있는 파일을 쓴다
                floor_of.setdefault((r.get("source"), r.get("source_listing_id")), r["floor"].strip())
    have_ids = {(c["source"], c["source_listing_id"]) for c in cands}
    seen: dict[tuple, set[str]] = {}
    for c in cands:
        seen.setdefault(same_key(c), set()).add(floor_of.get((c["source"], c["source_listing_id"]), ""))
    status = load_status()
    dests = base_destinations(univ)
    _, ref = _read(universities.ref_path(univ))
    univ_dests = {b["destination_id"] for b in ref if b["point_role"] == "building_poi"}
    by_addr, rfields = routes_by_address()

    drop, keep, reuse, need = Counter(), [], [], []
    for r in new_rows:
        sid = (r["source"], r["source_listing_id"])
        if sid in have_ids:
            drop["이미 후보에 있음"] += 1
            continue
        st = status.get(sid)
        if st and st.status == "unavailable":
            drop["거래 불가로 확인된 매물"] += 1
            continue
        if r["property_type"] not in TYPES:
            drop[f"유형 제외 ({r['property_type']})"] += 1
            continue
        if not _num(r["deposit_manwon"]) or float(r["deposit_manwon"]) > DEPOSIT_MAX:
            drop["보증금 3,000만 원 초과·미확인"] += 1
            continue
        k, fl = same_key(r), (r.get("floor") or "").strip()
        if is_same(seen.get(k, set()), fl):
            drop["같은 매물로 보임 (주소·보증금·월세·면적 같고 층 같음·미확인)"] += 1
            continue
        seen.setdefault(k, set()).add(fl)
        have_ids.add(sid)
        keep.append(r)
        legs = by_addr.get(r["address"], {})
        if all(d in legs for d in dests):
            # 이 대학 건물만 (기본 4곳 + 이미 부른 추가 강의동). 같은 주소를 다른 대학 건물로 부른 경로는 옮기지 않는다
            for d, leg in legs.items():
                if d in univ_dests:
                    reuse.append({**leg, "property_id": r["property_id"]})
        else:
            need.append(r)

    out_dir = DB / "staging" / "refill"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{univ}_{ts}"
    for name, rows in (("new", keep), ("needroute", need)):
        with (out_dir / f"{stem}_{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
    reuse_path = DB / "routes" / f"routes_reuse_{stem}.csv"
    with reuse_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rfields)
        w.writeheader()
        w.writerows(reuse)
    need_addr = {r["address"] for r in need}
    report = {
        "university": universities.get(univ)["name"], "listings": listings_rel, "candidates": candidates_rel,
        "collected": len(new_rows), "dropped": dict(drop), "new": len(keep),
        "route_reused": len(keep) - len(need), "need_route_listings": len(need), "need_route_addresses": len(need_addr),
        "max_kakao_calls": len(need_addr) * len(dests), "base_destinations": dests,
        "files": {"new": f"staging/refill/{stem}_new.csv", "needroute": f"staging/refill/{stem}_needroute.csv",
                  "reuse_routes": f"routes/{reuse_path.name}"},
    }
    (out_dir / f"{stem}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def plan(univ: str, needroute_rel: str, max_km: float, budget: int) -> None:
    """경로 부르기 전 주소만 확정 (주소 검색 쿼터는 따로·넉넉함) → 캠퍼스 직선 max_km 안, 가까운 순으로 budget 안에서 고른다.

    도보 경로 호출 수 = 고른 주소 수 × 기본 강의동 수. 직선거리는 호출을 줄이는 필터일 뿐 도보시간 대신 쓰지 않는다.
    """
    from app.config import get_settings  # noqa: PLC0415
    from app.tools.kakao import KakaoClient, single_exact_match  # noqa: PLC0415
    from pipeline.route_batch import _haversine_km  # noqa: PLC0415
    from app.tools.kakao import LatLng  # noqa: PLC0415

    cols, rows = _read(DB / needroute_rel)
    _, ref = _read(universities.ref_path(univ))
    dests = [b for b in ref if b["point_role"] == "building_poi" and b["csv_spot_name"]]
    campus = LatLng(lat=sum(float(b["latitude"]) for b in dests) / len(dests),
                    lng=sum(float(b["longitude"]) for b in dests) / len(dests))
    client = KakaoClient(get_settings().kakao_rest_api_key)
    km_of, cnt = {}, Counter()
    for addr in dict.fromkeys(r["address"] for r in rows):
        m = single_exact_match(client.search_address(addr, exact=True), addr)
        if m is None:
            cnt["주소 확정 불가"] += 1
            continue
        km = _haversine_km(m[0], campus)
        if km > max_km:
            cnt[f"캠퍼스 직선 {max_km:g}km 초과"] += 1
            continue
        km_of[addr] = km
    chosen = sorted(km_of, key=km_of.get)[: budget // len(dests)]
    out = DB / needroute_rel.replace("_needroute.csv", "_routeplan.csv")
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(r for r in rows if r["address"] in set(chosen))
    print(json.dumps({"university": universities.get(univ)["name"], "addresses": len(km_of) + sum(cnt.values()),
                      "excluded": dict(cnt), "routable": len(km_of), "chosen": len(chosen),
                      "calls": len(chosen) * len(dests), "left_for_later": len(km_of) - len(chosen),
                      "routeplan": str(out.relative_to(DB)).replace("\\", "/")}, ensure_ascii=False))


def _append(target_rel: str, add_rel: str, key: str, backup_dir) -> int:
    target, add = DB / target_rel, DB / add_rel
    cols, rows = _read(target)
    _, new = _read(add)
    have = {r[key] for r in rows} if key else set()
    new = [r for r in new if not key or r[key] not in have]
    shutil.copy2(target, backup_dir / f"{target.stem}_before_{datetime.now():%Y%m%dT%H%M%S}.csv")
    with target.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows + new)
    return len(new)


def merge(univ: str, new_rel: str, routes_rel: str, candidates_rel: str, walk_rel: str) -> None:
    stem = (DB / new_rel).stem.removesuffix("_new")
    name = f"refill/{stem}_candidates"
    build_candidates.main(new_rel, routes_rel, name, TYPES, DEPOSIT_MAX, univ)
    cand_rel = f"staging/{name}.csv"
    walk_name = f"refill/{stem}_walk"
    build_walk_table.main(cand_rel, walk_name, routes_rel.split(","))
    backup = DB / "staging" / "refill"
    n_c = _append(candidates_rel, cand_rel, "property_id", backup)
    # 도보시간 표: 새 매물 ID 만 덧붙인다 (기존 표에 없는 ID)
    _, existing = _read(DB / walk_rel)
    have_ids = {r["property_id"] for r in existing}
    _, wrows = _read(DB / f"staging/{walk_name}.csv")
    keep = [r for r in wrows if r["property_id"] not in have_ids]
    tmp = DB / "staging" / f"{walk_name}_add.csv"
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(wrows[0]) if wrows else build_walk_table.FIELDS)
        w.writeheader()
        w.writerows(keep)
    n_w = _append(walk_rel, f"staging/{walk_name}_add.csv", "", backup)
    print(json.dumps({"candidates_added": n_c, "walk_rows_added": n_w, "candidates": candidates_rel, "walk_table": walk_rel},
                     ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "plan", "merge"])
    ap.add_argument("--univ", required=True, choices=list(universities.UNIVERSITIES))
    ap.add_argument("--listings")
    ap.add_argument("--candidates")
    ap.add_argument("--needroute")
    ap.add_argument("--max-km", type=float, default=2.0)
    ap.add_argument("--budget", type=int, default=100000)
    ap.add_argument("--new")
    ap.add_argument("--routes")
    ap.add_argument("--walk-table")
    a = ap.parse_args()
    if a.step == "prepare":
        prepare(a.univ, a.listings, a.candidates)
    elif a.step == "plan":
        plan(a.univ, a.needroute, a.max_km, a.budget)
    else:
        merge(a.univ, a.new, a.routes, a.candidates, a.walk_table)


if __name__ == "__main__":
    main()
