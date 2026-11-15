"""광고 매물 ↔ 주변 전월세 실거래 비교 (Phase 7 Market Intelligence).

원칙
- 실거래는 과거 계약이고, 광고 매물은 현재 광고다. 둘을 섞지 않고 '참고 비교'로만 보여 준다.
- 비교 가능한 표본이 부족하면(MIN_N_JUDGE 미만) 가격 수준을 판단하지 않는다.
- 실거래 월세에는 관리비가 들어 있지 않다 → 매물의 '월세'만 비교하고 관리비는 따로 표시한다.
- 출처(국토교통부 전월세 실거래가, 공공데이터포털)와 조회 시점, 비교 조건을 함께 돌려준다.

비교 표본 (기본값, CompareConfig)
- 유형: 오피스텔 → 오피스텔 실거래, 원룸 등 → 단독/다가구 (+ 연립다세대가 있으면 함께)
- 월세 계약만 (월세 > 0), 최근 12개월 (데이터의 가장 최근 계약일 기준)
- 면적: 매물 면적 ±30%  · 보증금: |차이| ≤ max(500만 원, 매물 보증금의 50%)
- 범위: 같은 법정동 → 표본이 MIN_N_JUDGE 미만이면 같은 구로 넓힘 (scope 로 표시)
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.config import REPO_ROOT, _read
from app.models import Property

SGG_CODES = {"서대문구": "11410", "마포구": "11440", "성북구": "11290", "동대문구": "11230", "노원구": "11350", "중랑구": "11260"}
SOURCE = "국토교통부 전월세 실거래가 (공공데이터포털)"


@dataclass(frozen=True)
class CompareConfig:
    months: int = 12
    area_tol: float = 0.30
    deposit_abs_manwon: float = 500.0
    deposit_rel: float = 0.50
    min_n_judge: int = 10   # 이보다 적으면 수준 판단 안 함
    min_n_show: int = 3     # 이보다 적으면 분포도 보여 주지 않음
    low_pct: float = 25.0
    high_pct: float = 75.0


CONFIG = CompareConfig()


@dataclass(frozen=True)
class Tx:
    tx_type: str
    sgg_cd: str
    umd_nm: str
    area_m2: float
    deposit: float
    rent: float
    contract_date: date
    house_type: str
    fetched_at: str


def _f(v: str) -> float | None:
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def _tx_sources() -> list[tuple[Path, set[str] | None]]:
    """(파일, 이 파일에서 읽을 유형). 유형 None = 파일의 모든 행.

    MARKET_TX_PATH 가 있으면 그 파일 전부. 없으면 DB/staging 의 rent_tx_*.csv 중 **유형별로 가장 최근 파일**만 쓴다
    (유형마다 따로 수집할 수 있고, 같은 기간을 다시 받아도 중복으로 세지 않게). 유형은 옆의 .report.json 에서 읽는다.
    """
    override = (_read("MARKET_TX_PATH") or "").strip()
    if override:
        out = []
        for p in override.split(","):
            pp = Path(p.strip())
            out.append((pp if pp.is_absolute() else (REPO_ROOT / "BE" / pp).resolve(), None))
        return out
    chosen: list[tuple[Path, set[str] | None]] = []
    covered: set[str] = set()
    for path in sorted((REPO_ROOT / "DB" / "staging").glob("rent_tx_*.csv"), reverse=True):
        try:
            rep = json.loads(path.with_suffix(".report.json").read_text(encoding="utf-8"))
            types = {t for t, n in rep.get("by_type", {}).items() if n}
        except (OSError, ValueError):
            continue
        new = types - covered
        if new:
            chosen.append((path, new))
            covered |= new
    return chosen


@lru_cache
def load_transactions() -> tuple[Tx, ...]:
    out: list[Tx] = []
    for path, types in _tx_sources():
        if not path.exists():
            continue
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            if types is not None and r["tx_type"] not in types:
                continue
            area, dep, rent = _f(r["area_m2"]), _f(r["deposit_manwon"]), _f(r["monthly_rent_manwon"])
            if area is None or dep is None or rent is None or not r["contract_date"]:
                continue
            out.append(Tx(r["tx_type"], r["sgg_cd"], r["umd_nm"], area, dep, rent,
                          date.fromisoformat(r["contract_date"]), r["house_type"], r["fetched_at"]))
    return tuple(out)


def address_parts(address: str) -> tuple[str | None, str | None]:
    """'서울특별시 성북구 안암동5가 103-1' → ('11290', '안암동5가'). 모르는 구면 (None, 동)."""
    m = re.search(r"(\S+구)\s+(\S+(?:동|가|로)\d*가?)\b", address or "")
    if not m:
        return None, None
    return SGG_CODES.get(m.group(1)), m.group(2)


def tx_types_for(property_type: str) -> list[str]:
    return ["offi"] if "오피스텔" in (property_type or "") else ["sh", "rh"]


class CompSample(BaseModel):
    contract_date: str
    umd_nm: str
    tx_type: str
    area_m2: float
    deposit_manwon: float
    monthly_rent_manwon: float


class MarketCompare(BaseModel):
    property_id: str
    available: bool
    reason: str | None = None            # 비교하지 못한 이유
    scope: str | None = None             # "동" | "구"
    criteria: dict = {}
    n: int = 0
    listing_monthly_rent_manwon: float | None = None
    listing_maintenance_manwon: float | None = None
    rent_median: float | None = None
    rent_p25: float | None = None
    rent_p75: float | None = None
    deposit_median: float | None = None
    listing_percentile: float | None = None  # 표본 중 이 매물보다 월세가 낮은 비율 (동률은 절반)
    level: str | None = None             # "주변 실거래보다 낮은 편" | "비슷한 편" | "높은 편" | None(판단 안 함)
    period: list[str] = []
    recent: list[CompSample] = []
    source: str = SOURCE
    fetched_at: str | None = None
    notes: list[str] = []


def _q(values: list[float], q: float) -> float:
    if len(values) == 1:
        return values[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1], 2)


def compare_property(p: Property, txs: tuple[Tx, ...] | None = None, cfg: CompareConfig = CONFIG) -> MarketCompare:
    txs = load_transactions() if txs is None else txs
    base = MarketCompare(property_id=p.property_id, available=False,
                         listing_monthly_rent_manwon=p.monthly_rent_manwon, listing_maintenance_manwon=p.maintenance_manwon)
    if not txs:
        base.reason = "실거래 데이터가 없습니다 (수집 전)."
        return base
    sgg, dong = address_parts(p.address)
    if sgg is None:
        base.reason = "실거래를 수집한 구에 있는 매물이 아닙니다."
        return base
    if p.monthly_rent_manwon is None or p.area_m2 is None:
        base.reason = "매물의 월세 또는 면적 정보가 없어 비교할 수 없습니다."
        return base
    types = tx_types_for(p.property_type)
    latest = max(t.contract_date for t in txs)
    since = latest - timedelta(days=round(cfg.months * 30.44))
    lo, hi = p.area_m2 * (1 - cfg.area_tol), p.area_m2 * (1 + cfg.area_tol)
    dep_tol = None if p.deposit_manwon is None else max(cfg.deposit_abs_manwon, p.deposit_manwon * cfg.deposit_rel)

    def ok(t: Tx) -> bool:
        return (t.tx_type in types and t.sgg_cd == sgg and t.rent > 0 and t.contract_date >= since and lo <= t.area_m2 <= hi
                and (dep_tol is None or abs(t.deposit - p.deposit_manwon) <= dep_tol))  # type: ignore[operator]

    pool = [t for t in txs if ok(t)]
    comps, scope = [t for t in pool if t.umd_nm == dong], "동"
    if len(comps) < cfg.min_n_judge:
        comps, scope = pool, "구"
    criteria = {
        "types": types, "sgg_cd": sgg, "umd_nm": dong if scope == "동" else None, "monthly_only": True,
        "area_m2": [round(lo, 1), round(hi, 1)], "deposit_manwon": None if dep_tol is None
        else [round(max(0.0, p.deposit_manwon - dep_tol), 1), round(p.deposit_manwon + dep_tol, 1)],  # type: ignore[operator]
        "since": since.isoformat(), "until": latest.isoformat(),
    }
    notes = ["실거래 월세에는 관리비가 들어 있지 않아 매물의 월세만 비교했습니다 (관리비는 따로 표시).",
             "실거래는 지난 계약 기록이며, 광고 매물의 실제 계약 가능 여부·가격과 다를 수 있습니다."]
    if "sh" in types and not any(t.tx_type == "rh" for t in txs):
        notes.append("연립다세대 실거래는 아직 수집하지 않아 단독/다가구 거래만 비교했습니다.")
    if "sh" in types:
        notes.append("단독/다가구 실거래 면적은 계약면적이라 매물의 전용면적과 기준이 조금 다를 수 있습니다.")
    if scope == "구":
        notes.append(f"같은 동 표본이 {cfg.min_n_judge}건 미만이라 같은 구 전체로 넓혀 비교했습니다.")
    if len(comps) < cfg.min_n_show:
        return base.model_copy(update={"reason": f"비교할 수 있는 실거래가 {len(comps)}건뿐입니다.", "scope": scope,
                                       "criteria": criteria, "n": len(comps), "notes": notes})

    rents = sorted(t.rent for t in comps)
    lower = sum(r < p.monthly_rent_manwon for r in rents)
    equal = sum(r == p.monthly_rent_manwon for r in rents)
    pct = round((lower + 0.5 * equal) / len(rents) * 100, 1)
    level = None
    if len(comps) >= cfg.min_n_judge:
        level = ("주변 실거래보다 낮은 편" if pct < cfg.low_pct else "주변 실거래보다 높은 편" if pct > cfg.high_pct
                 else "주변 실거래와 비슷한 편")
    else:
        notes.append(f"표본이 {len(comps)}건이라 가격 수준은 판단하지 않았습니다 (분포만 참고).")
    recent = sorted(comps, key=lambda t: t.contract_date, reverse=True)[:5]
    return MarketCompare(
        property_id=p.property_id, available=True, scope=scope, criteria=criteria, n=len(comps),
        listing_monthly_rent_manwon=p.monthly_rent_manwon, listing_maintenance_manwon=p.maintenance_manwon,
        rent_median=round(statistics.median(rents), 2), rent_p25=_q(rents, 25), rent_p75=_q(rents, 75),
        deposit_median=round(statistics.median(t.deposit for t in comps), 2), listing_percentile=pct, level=level,
        period=[min(t.contract_date for t in comps).isoformat(), max(t.contract_date for t in comps).isoformat()],
        recent=[CompSample(contract_date=t.contract_date.isoformat(), umd_nm=t.umd_nm, tx_type=t.tx_type, area_m2=t.area_m2,
                           deposit_manwon=t.deposit, monthly_rent_manwon=t.rent) for t in recent],
        fetched_at=max(t.fetched_at for t in comps), notes=notes,
    )
