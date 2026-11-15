"""지하철역 · 버스정류장 · 도시공원 위치 (공공데이터포털 파일/표준데이터) → 매물 주변 거리 계산용.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_poi              # 내려받고 정규화
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_poi --raw-dir DB/raw/poi/20261115   # 이미 받은 원본으로 정규화만

원천 (모두 '이용허락범위 제한 없음', 로그인·키 불필요):
- 지하철역: 전국도시철도역사정보표준데이터 (data.go.kr 15013205, 국가철도공단). 데이터기준일 2019~2021 →
            그 뒤 개통한 노선·역(우이신설·신림선 등 일부)은 없을 수 있다. 좌표는 역사 대표점이며 출입구가 아니다.
- 버스정류장: 국토교통부_전국 버스정류장 위치정보 (data.go.kr 15067528, 파일 CSV, cp949).
- 도시공원: 전국도시공원정보표준데이터 (data.go.kr 15012890). 좌표는 공원 대표점이라 큰 공원은 실제 입구까지 더 가깝거나 멀 수 있다.

검증:
- 수도권(서울·경기·인천) 주소/도시만 남긴다. 원본에 다른 지역 역이 서울 좌표로 들어간 오류가 있다
  (예: 대구 1호선 '신천', 부산 동해선 '교대' — 2026-11-15 확인) → 주소 시도와 맞지 않으면 버린다.
- 좌표가 없거나 숫자가 아니면 버린다 (0 으로 채우지 않음). 버린 건수와 사유는 report 에 남긴다.

출력: 원본 DB/raw/poi/<ts>/ (append-only, 커밋 금지) · 정규화 DB/staging/poi_<ts>.csv + .report.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT

DB = REPO_ROOT / "DB"
PORTAL = "https://www.data.go.kr"
CAPITAL = ("서울", "경기", "인천")
# 수도권 대략 범위 (주소가 비어 있는 행의 보조 확인용)
LAT_RANGE, LON_RANGE = (36.9, 38.3), (126.3, 127.9)
STANDARD = {"subway": "15013205", "park": "15012890"}
BUS_FILE = {"publicDataPk": "15067528", "publicDataDetailPk": "uddi:ed6af349-4869-4c9a-82e1-82ecc32c686e"}
FIELDS = ["poi_id", "kind", "name", "detail", "lat", "lon", "address", "area_m2", "ref_date", "source", "raw_file"]
SOURCES = {
    "subway": "전국도시철도역사정보표준데이터 (공공데이터포털 15013205)",
    "bus_stop": "국토교통부_전국 버스정류장 위치정보 (공공데이터포털 15067528)",
    "park": "전국도시공원정보표준데이터 (공공데이터포털 15012890)",
}


def _client() -> httpx.Client:
    return httpx.Client(timeout=180, follow_redirects=True,
                        headers={"X-Requested-With": "XMLHttpRequest", "User-Agent": "home-poc-pipeline"})


def download_standard(c: httpx.Client, pk: str, out: Path) -> Path:
    """표준데이터 전체 내려받기 (포털 '전체 파일 다운로드' 와 같은 요청)."""
    h = c.get(f"{PORTAL}/download/columList.json", params={"pk": pk, "ext": "CSV"}).json()
    if "tableVO" not in h:
        raise SystemExit(f"표준데이터 {pk} 헤더 실패: {str(h)[:200]}")
    t, total = h["tableVO"], int(h["totalCount"])
    rows: list[dict] = []
    page = 1
    while len(rows) < total:
        got = c.get(f"{PORTAL}/download/standard.json", params={
            "colNmList": t["colNmList"], "totalCount": total, "svcTableNm": t["svcTableNm"],
            "perPage": 10000, "page": page, "publicDataPk": pk}).json()
        if not got:
            break
        rows += got
        page += 1
    path = out / f"standard_{pk}.json"
    path.write_text(json.dumps({"fileName": h.get("fileName"), "columns": h["columList"], "totalCount": total, "rows": rows},
                               ensure_ascii=False), encoding="utf-8")
    print(f"  {h.get('fileName')}: {len(rows)}/{total}행")
    return path


def download_bus(c: httpx.Client, out: Path) -> Path:
    meta = c.post(f"{PORTAL}/tcs/dss/selectFileDataDownload.do",
                  data={**BUS_FILE, "atchFileId": "", "fileDetailSn": "1", "publicDataTyCode": "PR0051"}).json()
    if not meta.get("status"):
        raise SystemExit(f"버스정류장 파일 정보 실패: {str(meta)[:200]}")
    r = c.get(f"{PORTAL}/cmm/cmm/fileDownload.do", params={"atchFileId": meta["atchFileId"], "fileDetailSn": meta["fileDetailSn"]})
    r.raise_for_status()
    path = out / f"bus_stops_{meta['dataSetFileDetailInfo'].get('dataNm', 'file')}.csv".replace(" ", "_")
    path.write_bytes(r.content)
    print(f"  버스정류장 파일: {len(r.content):,} bytes")
    return path


def _num(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if x else None


def _in_capital(lat: float, lon: float) -> bool:
    return LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]


def normalize(raw_dir: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    dropped: Counter = Counter()

    def keep(kind: str, region_text: str, lat, lon) -> tuple[float, float] | None:
        la, lo = _num(lat), _num(lon)
        if la is None or lo is None:
            dropped[f"{kind}:no_coord"] += 1
            return None
        if region_text and not region_text.startswith(CAPITAL):
            # 수도권 밖 주소. 서울 좌표가 붙은 다른 시도 역(원본 오류)도 여기서 빠진다.
            dropped[f"{kind}:outside_capital"] += 1
            return None
        if not _in_capital(la, lo):
            dropped[f"{kind}:coord_outside_capital"] += 1
            return None
        return la, lo

    sub = next(raw_dir.glob(f"standard_{STANDARD['subway']}.json"), None)
    if sub:
        d = json.loads(sub.read_text(encoding="utf-8"))
        for x in d["rows"]:
            ll = keep("subway", (x.get("RDNMADR") or "").strip(), x.get("LATITUDE"), x.get("LONGITUDE"))
            if ll:
                rows.append({"poi_id": f"subway:{x.get('ROUTE_NO')}:{x.get('STATN_NO')}", "kind": "subway",
                             "name": (x.get("STATN_NM") or "").strip().removesuffix("역") or x.get("STATN_NM"),
                             "detail": x.get("ROUTE_NM") or "", "lat": ll[0], "lon": ll[1], "address": x.get("RDNMADR") or "",
                             "area_m2": "", "ref_date": x.get("REFERENCE_DATE") or "", "source": SOURCES["subway"],
                             "raw_file": str(sub.relative_to(DB)).replace("\\", "/")})
    park = next(raw_dir.glob(f"standard_{STANDARD['park']}.json"), None)
    if park:
        d = json.loads(park.read_text(encoding="utf-8"))
        for x in d["rows"]:
            region = (x.get("INSTT_NM") or x.get("LNMADR") or x.get("RDNMADR") or "").strip()
            ll = keep("park", region, x.get("LATITUDE"), x.get("LONGITUDE"))
            if ll:
                rows.append({"poi_id": f"park:{x.get('MANAGE_NO')}", "kind": "park", "name": (x.get("PARK_NM") or "").strip(),
                             "detail": x.get("PARK_SE") or "", "lat": ll[0], "lon": ll[1],
                             "address": x.get("LNMADR") or x.get("RDNMADR") or "", "area_m2": _num(x.get("PARK_AR")) or "",
                             "ref_date": x.get("REFERENCE_DATE") or "", "source": SOURCES["park"],
                             "raw_file": str(park.relative_to(DB)).replace("\\", "/")})
    for bus in sorted(raw_dir.glob("bus_stops_*.csv")) + sorted(raw_dir.glob("station*.csv")):
        text = bus.read_bytes()
        try:
            s = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            s = text.decode("cp949")
        for x in csv.DictReader(s.splitlines()):
            ll = keep("bus_stop", (x.get("도시명") or "").strip(), x.get("위도"), x.get("경도"))
            if ll:
                rows.append({"poi_id": f"bus:{x.get('정류장번호')}", "kind": "bus_stop", "name": (x.get("정류장명") or "").strip(),
                             "detail": x.get("모바일단축번호") or "", "lat": ll[0], "lon": ll[1], "address": x.get("도시명") or "",
                             "area_m2": "", "ref_date": x.get("정보수집일") or "", "source": SOURCES["bus_stop"],
                             "raw_file": str(bus.relative_to(DB)).replace("\\", "/")})
        break   # 파일 하나만
    # 같은 poi_id 중복 제거
    seen: set[str] = set()
    uniq = []
    for r in rows:
        if r["poi_id"] in seen:
            dropped[f"{r['kind']}:duplicate_id"] += 1
            continue
        seen.add(r["poi_id"])
        uniq.append(r)
    return uniq, dict(dropped)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", help="이미 받은 원본 폴더 (DB/ 기준 또는 절대 경로). 주면 내려받지 않는다.")
    a = ap.parse_args()
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    if a.raw_dir:
        raw_dir = Path(a.raw_dir)
        raw_dir = raw_dir if raw_dir.is_absolute() else (REPO_ROOT / raw_dir)
    else:
        raw_dir = DB / "raw" / "poi" / ts
        raw_dir.mkdir(parents=True, exist_ok=False)
        with _client() as c:
            for pk in STANDARD.values():
                download_standard(c, pk, raw_dir)
            download_bus(c, raw_dir)
    rows, dropped = normalize(raw_dir)
    out = DB / "staging" / f"poi_{ts}.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    by_kind = Counter(r["kind"] for r in rows)
    refs = {k: sorted({r["ref_date"][:10] for r in rows if r["kind"] == k and r["ref_date"]}) for k in by_kind}
    report = {"created_at": ts, "raw_dir": str(raw_dir.relative_to(REPO_ROOT)).replace("\\", "/"),
              "output": str(out.relative_to(REPO_ROOT)).replace("\\", "/"), "by_kind": dict(by_kind), "dropped": dropped,
              "ref_date_range": {k: [v[0], v[-1]] if v else None for k, v in refs.items()}, "sources": SOURCES,
              "note": "수도권만. 좌표는 역사·정류장·공원 대표점이며 거리는 직선거리. 지하철 표준데이터는 2019~2021 기준이라 이후 개통 역은 없을 수 있음."}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
