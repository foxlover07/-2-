"""국토교통부 전월세 실거래가 수집 (공공데이터포털) → 원본 XML 보존 + 정규화 CSV.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.collect_rent [--types sh,rh,offi] [--lawd 11410,11440]
        [--end 202610] [--months 12] [--max-calls 400]

- 실거래가는 '과거 계약'이다. 광고 매물(현재 매물)과 섞지 않고, 입주 가능 매물처럼 표시하지 않는다.
- 원본: DB/raw/molit/<ts>/<type>_<lawd>_<yyyymm>_p<page>.xml (append-only, 커밋 금지)
- 정규화: DB/staging/rent_tx_<ts>.csv + .report.json (유형·구·월별 건수, 실패 목록)
- 활용신청이 안 된 유형(not_registered)은 건너뛰고 report 에 남긴다. 한도 초과·연결 실패면 즉시 멈춘다.
- 금액 단위 만원. 값이 비어 있으면 빈 칸(None) — 0 으로 채우지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import httpx

from app.config import REPO_ROOT
from app.publicdata import PublicDataError, get, xml_items

DB = REPO_ROOT / "DB"
BASE = "https://apis.data.go.kr/1613000"
TYPES = {
    # 키: (서비스, 오퍼레이션, 이름)
    "sh": ("RTMSDataSvcSHRent", "getRTMSDataSvcSHRent", "단독/다가구"),
    "rh": ("RTMSDataSvcRHRent", "getRTMSDataSvcRHRent", "연립다세대"),
    "offi": ("RTMSDataSvcOffiRent", "getRTMSDataSvcOffiRent", "오피스텔"),
}
# 4개 대학 매물이 있는 구 (법정동코드 앞 5자리). 서대문·마포(이대·서강대), 성북·동대문(고려대), 노원·중랑(서울과기대)
DEFAULT_LAWD = ["11410", "11440", "11290", "11230", "11350", "11260"]
PAGE_SIZE = 1000

FIELDS = [
    "tx_type", "tx_type_name", "sgg_cd", "sgg_nm", "umd_nm", "jibun", "building_name", "house_type",
    "area_m2", "area_kind", "deposit_manwon", "monthly_rent_manwon", "rent_kind", "contract_date",
    "contract_type", "contract_term", "build_year", "floor", "source", "fetched_at", "raw_file",
]


def _num(v: str | None) -> float | None:
    v = (v or "").replace(",", "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def normalize(tx_type: str, it: dict[str, str], fetched_at: str, raw_file: str) -> dict:
    deposit, rent = _num(it.get("deposit")), _num(it.get("monthlyRent"))
    if tx_type == "sh":
        area, kind = _num(it.get("totalFloorAr")), "계약면적(totalFloorAr)"
        house = it.get("houseType") or ""
    else:
        area, kind = _num(it.get("excluUseAr")), "전용면적(excluUseAr)"
        house = it.get("houseType") or ("오피스텔" if tx_type == "offi" else "")
    y, m, d = it.get("dealYear", ""), it.get("dealMonth", ""), it.get("dealDay", "")
    date = f"{y}-{int(m):02d}-{int(d):02d}" if y and m and d else ""
    return {
        "tx_type": tx_type, "tx_type_name": TYPES[tx_type][2], "sgg_cd": it.get("sggCd", ""), "sgg_nm": it.get("sggNm", ""),
        "umd_nm": it.get("umdNm", ""), "jibun": it.get("jibun", ""),
        "building_name": it.get("offiNm") or it.get("mhouseNm") or "", "house_type": house,
        "area_m2": area, "area_kind": kind, "deposit_manwon": deposit, "monthly_rent_manwon": rent,
        "rent_kind": "월세" if rent and rent > 0 else ("전세" if rent == 0 else ""),
        "contract_date": date, "contract_type": it.get("contractType", ""), "contract_term": it.get("contractTerm", ""),
        "build_year": it.get("buildYear", ""), "floor": it.get("floor", ""),
        "source": f"국토교통부_{TYPES[tx_type][2]} 전월세 실거래가 (공공데이터포털)", "fetched_at": fetched_at, "raw_file": raw_file,
    }


def months_back(end: str, n: int) -> list[str]:
    y, m = int(end[:4]), int(end[4:])
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return sorted(out)


def main(types: list[str], lawd: list[str], end: str, n_months: int, max_calls: int, db: Path = DB) -> Path:
    ymds = months_back(end, n_months)
    planned = len(types) * len(lawd) * len(ymds)
    print(f"유형 {types} × 구 {len(lawd)} × 월 {len(ymds)} ({ymds[0]}~{ymds[-1]}) → 최소 호출 {planned}건 (페이지 더 있으면 추가)")
    if planned > max_calls:
        raise SystemExit(f"예정 호출 {planned} > 상한 {max_calls}. --max-calls 를 올린다.")
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_dir = db / "raw" / "molit" / ts
    raw_dir.mkdir(parents=True, exist_ok=False)
    out = db / "staging" / f"rent_tx_{ts}.csv"
    calls, rows_n = 0, 0
    counts: dict[str, int] = {}
    skipped: dict[str, str] = {}
    with out.open("w", encoding="utf-8-sig", newline="") as f, httpx.Client(timeout=30) as client:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for t in types:
            svc, op, name = TYPES[t]
            for code in lawd:
                for ym in ymds:
                    page = 1
                    while True:
                        if calls >= max_calls:
                            raise SystemExit(f"호출 상한 {max_calls} 도달. 지금까지 결과: {out}")
                        try:
                            text = get(f"{BASE}/{svc}/{op}", {"LAWD_CD": code, "DEAL_YMD": ym, "numOfRows": str(PAGE_SIZE),
                                                              "pageNo": str(page)}, client=client)
                        except PublicDataError as e:
                            calls += 1
                            if e.kind == "not_registered":
                                skipped[t] = str(e)
                                print(f"{name}: 활용신청 미승인 → 건너뜀")
                                break
                            raise SystemExit(f"{name} {code} {ym} 실패 ({e.kind}): {e}") from e
                        calls += 1
                        raw = raw_dir / f"{t}_{code}_{ym}_p{page}.xml"
                        raw.write_text(text, encoding="utf-8")
                        items, total, _ = xml_items(text)
                        fetched = datetime.now().astimezone().isoformat(timespec="seconds")
                        for it in items:
                            w.writerow(normalize(t, it, fetched, str(raw.relative_to(db))))
                        rows_n += len(items)
                        key = f"{t}|{code}|{ym}"
                        counts[key] = counts.get(key, 0) + len(items)
                        if total is None or page * PAGE_SIZE >= total or not items:
                            break
                        page += 1
                    if t in skipped:
                        break
                if t in skipped:
                    break
                print(f"  {name} {code}: {sum(v for k, v in counts.items() if k.startswith(f'{t}|{code}|'))}건 (누적 호출 {calls})")
    report = {
        "created_at": ts, "output": str(out.relative_to(db)), "raw_dir": str(raw_dir.relative_to(db)),
        "months": [ymds[0], ymds[-1]], "lawd_cd": lawd, "types": types, "calls": calls, "rows": rows_n,
        "by_type": {t: sum(v for k, v in counts.items() if k.startswith(f"{t}|")) for t in types},
        "by_type_lawd": {f"{t}|{c}": sum(v for k, v in counts.items() if k.startswith(f"{t}|{c}|")) for t in types for c in lawd},
        "skipped_not_registered": skipped,
        "note": "과거 전월세 계약(실거래). 광고 매물과 별도 데이터. 단독/다가구 면적은 계약면적, 연립다세대·오피스텔은 전용면적.",
    }
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"완료: 호출 {calls}, 행 {rows_n} → {out}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="sh,rh,offi")
    ap.add_argument("--lawd", default=",".join(DEFAULT_LAWD))
    ap.add_argument("--end", default=datetime.now().strftime("%Y%m"))
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--max-calls", type=int, default=400)
    a = ap.parse_args()
    main([t for t in a.types.split(",") if t], [c for c in a.lawd.split(",") if c], a.end, a.months, a.max_calls)
