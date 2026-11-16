"""소상공인시장진흥공단 상가(상권)정보 수집 → 생활 편의시설 계산용 (원본 JSON 보존 + 정규화 CSV).

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_stores [--sgg 11410,11440] [--max-calls 300]

- 매물마다 반경 조회하지 않고, 구(시군구) 단위로 업종별 전체 목록을 한 번 받아 거리는 서버에서 계산한다 (호출 수 절약).
- 매물이 있는 6개 구 + 경계 매물을 위해 맞닿은 구도 함께 받는다.
- 원본: DB/raw/sbiz/<ts>/<category>_<sgg>_p<page>.json · 정규화: DB/staging/stores_<ts>.csv + .report.json (커밋 금지)
- 상가정보는 '등록된 업소' 기준(기준연월 stdrYm)이며 실제 영업 여부는 보장하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT
from app.publicdata import PublicDataError, check_error, service_key

DB = REPO_ROOT / "DB"
URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"
PAGE_SIZE = 1000

# 키: (이름, 조회 조건). 코드는 2026-11-15 이대 주변 실제 응답에서 확인한 값.
CATEGORIES: dict[str, tuple[str, dict[str, str]]] = {
    "convenience": ("편의점", {"indsSclsCd": "G20405"}),
    "supermarket": ("슈퍼마켓", {"indsSclsCd": "G20404"}),
    "pharmacy": ("약국", {"indsSclsCd": "G21501"}),
    "laundry": ("세탁소", {"indsSclsCd": "S20901"}),
    "laundromat": ("셀프 빨래방", {"indsSclsCd": "S20902"}),
    "clinic": ("의원", {"indsMclsCd": "Q102"}),
    "bar": ("주점", {"indsMclsCd": "I211"}),
    "karaoke": ("노래방", {"indsSclsCd": "R10407"}),
    "gym": ("헬스장", {"indsSclsCd": "R10307"}),  # 2026-11-15 서대문구 응답에서 확인 (스포츠 서비스 > 헬스장)
}
# 매물이 있는 구: 서대문 마포 성북 동대문 노원 중랑 / 맞닿은 구: 은평 종로 중구 용산 성동 강북 도봉
DEFAULT_SGG = ["11410", "11440", "11290", "11230", "11350", "11260",
               "11380", "11110", "11140", "11170", "11200", "11305", "11320"]
FIELDS = ["store_id", "name", "branch", "category", "category_name", "inds_mcls_nm", "inds_scls_cd", "inds_scls_nm",
          "sgg_cd", "sgg_nm", "ldong_nm", "lno_adr", "rdnm_adr", "floor", "lon", "lat", "stdr_ym", "fetched_at", "raw_file"]


def main(sgg: list[str], cats: list[str], max_calls: int, db: Path = DB) -> Path:
    print(f"구 {len(sgg)} × 업종 {len(cats)} → 최소 호출 {len(sgg) * len(cats)}건")
    if len(sgg) * len(cats) > max_calls:
        raise SystemExit(f"예정 호출 > 상한 {max_calls}")
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_dir = db / "raw" / "sbiz" / ts
    raw_dir.mkdir(parents=True, exist_ok=False)
    out = db / "staging" / f"stores_{ts}.csv"
    key = service_key()
    calls, seen, counts, stdr = 0, set(), {}, set()
    with out.open("w", encoding="utf-8-sig", newline="") as f, httpx.Client(timeout=60) as client:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for cat in cats:
            name, cond = CATEGORIES[cat]
            for code in sgg:
                page = 1
                while True:
                    if calls >= max_calls:
                        raise SystemExit(f"호출 상한 {max_calls} 도달 → {out}")
                    r = client.get(URL, params={"serviceKey": key, "type": "json", "divId": "signguCd", "key": code,
                                                "numOfRows": str(PAGE_SIZE), "pageNo": str(page), **cond})
                    calls += 1
                    try:
                        check_error(r.text)
                        d = r.json()
                    except PublicDataError as e:
                        raise SystemExit(f"{name} {code} 실패 ({e.kind}): {e}") from e
                    except ValueError:
                        # 결과 없음은 JSON 대신 안내 문구가 올 수 있다 → 0건으로 본다
                        print(f"  {name} {code}: 응답이 JSON 이 아님 (0건으로 처리): {r.text[:80]!r}")
                        break
                    raw = raw_dir / f"{cat}_{code}_p{page}.json"
                    raw.write_text(r.text, encoding="utf-8")
                    body = d.get("body") or {}
                    items = body.get("items") or []
                    stdr.add(d.get("header", {}).get("stdrYm"))
                    fetched = datetime.now().astimezone().isoformat(timespec="seconds")
                    for it in items:
                        sid = it.get("bizesId")
                        if (sid, cat) in seen or it.get("lon") is None or it.get("lat") is None:
                            continue
                        seen.add((sid, cat))
                        w.writerow({
                            "store_id": sid, "name": it.get("bizesNm", ""), "branch": it.get("brchNm", ""), "category": cat,
                            "category_name": name, "inds_mcls_nm": it.get("indsMclsNm", ""), "inds_scls_cd": it.get("indsSclsCd", ""),
                            "inds_scls_nm": it.get("indsSclsNm", ""), "sgg_cd": it.get("signguCd", ""), "sgg_nm": it.get("signguNm", ""),
                            "ldong_nm": it.get("ldongNm", ""), "lno_adr": it.get("lnoAdr", ""), "rdnm_adr": it.get("rdnmAdr", ""),
                            "floor": it.get("flrNo", ""), "lon": it["lon"], "lat": it["lat"], "stdr_ym": d.get("header", {}).get("stdrYm", ""),
                            "fetched_at": fetched, "raw_file": str(raw.relative_to(db)),
                        })
                        counts[cat] = counts.get(cat, 0) + 1
                    total = int(body.get("totalCount") or 0)
                    if page * PAGE_SIZE >= total or not items:
                        break
                    page += 1
            print(f"  {name}: {counts.get(cat, 0)}곳 (누적 호출 {calls})")
    report = {"created_at": ts, "output": str(out.relative_to(db)), "raw_dir": str(raw_dir.relative_to(db)), "sgg": sgg,
              "calls": calls, "by_category": counts, "stdr_ym": sorted(x for x in stdr if x),
              "note": "상가(상권)정보 등록 업소 기준. 실제 영업 여부 미보장. 주점·노래방 수는 소음 수준이 아니다."}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: 호출 {calls}, 업소 {sum(counts.values())} → {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sgg", default=",".join(DEFAULT_SGG))
    ap.add_argument("--categories", default=",".join(CATEGORIES))
    ap.add_argument("--max-calls", type=int, default=300)
    a = ap.parse_args()
    main([c for c in a.sgg.split(",") if c], [c for c in a.categories.split(",") if c], a.max_calls)
