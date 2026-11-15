"""매물 지번 ↔ 건축물대장 표제부 (pipeline.collect_building 결과). 추천 점수에는 쓰지 않는 참고 정보.

- 광고 매물의 지번(location_precision=lot) 기준이다. 광고 주소가 실제 건물과 다르면 다른 건물 정보일 수 있다.
- 한 지번에 주건축물이 여러 개면 어느 건물인지 모르므로 값을 보여 주지 않는다 (multiple).
- 대장상 주용도는 사실만 보여 준다. 주거 가능 여부·불법 여부를 판정하지 않는다.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.config import REPO_ROOT, _read
from app.models import Property

SOURCE = "국토교통부 건축HUB 건축물대장 표제부 (공공데이터포털)"
_ADDR_RE = re.compile(r"(\S+구)\s+(\S+?)\s+(산\s*)?(\d+)(?:-(\d+))?\s*$")


def address_key(address: str | None) -> str | None:
    """'서울특별시 서대문구 대현동 53-33' → '서대문구|대현동|0|53|33'. 지번 형식이 아니면 None."""
    m = _ADDR_RE.search(address or "")
    if not m:
        return None
    return f"{m.group(1)}|{m.group(2)}|{'1' if m.group(3) else '0'}|{int(m.group(4))}|{int(m.group(5) or 0)}"


def _paths() -> list[Path]:
    override = (_read("BUILDING_TITLES_PATH") or "").strip()
    if override:
        return [Path(p.strip()) if Path(p.strip()).is_absolute() else (REPO_ROOT / "BE" / p.strip()).resolve()
                for p in override.split(",") if p.strip()]
    return sorted((REPO_ROOT / "DB" / "staging").glob("building_titles_*.csv"))


@lru_cache
def load_titles() -> dict[str, dict[str, str]]:
    """지번 키 → 가장 최근 파일의 성공 행 (error·dong_unresolved 는 더 오래된 성공 행을 덮지 않는다)."""
    out: dict[str, dict[str, str]] = {}
    for path in _paths():  # 오래된 파일부터 → 최근 파일이 덮어쓴다
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = address_key(r.get("address"))
                if key is None:
                    continue
                if r["match_status"] in ("error", "dong_unresolved") and key in out:
                    continue
                out[key] = r
    return out


class BuildingInfo(BaseModel):
    property_id: str
    available: bool
    match_status: str | None = None   # matched · multiple · no_record · dong_unresolved · error
    reason: str | None = None
    building_name: str | None = None
    main_purpose: str | None = None
    detail_purpose: str | None = None
    use_approval_date: str | None = None   # YYYY-MM-DD (사용승인일)
    use_approval_year: int | None = None
    floors_above: int | None = None
    floors_below: int | None = None
    elevators: int | None = None
    parking: int | None = None
    households: int | None = None
    structure: str | None = None
    total_area_m2: float | None = None
    source: str = SOURCE
    fetched_at: str | None = None
    notes: list[str] = []


def _i(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _f(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


REASONS = {
    "multiple": "이 지번에 주건축물이 여러 개라 어느 건물인지 알 수 없습니다.",
    "no_record": "이 지번으로 등록된 건축물대장 표제부가 없습니다.",
    "dong_unresolved": "법정동 코드를 찾지 못해 조회하지 못했습니다.",
    "error": "조회 중 오류가 나서 정보가 없습니다.",
}


def building_for(p: Property, titles: dict[str, dict[str, str]] | None = None) -> BuildingInfo:
    titles = load_titles() if titles is None else titles
    if not titles:
        return BuildingInfo(property_id=p.property_id, available=False, reason="건축물대장 데이터가 없습니다 (수집 전).")
    key = address_key(p.address)
    r = titles.get(key) if key else None
    if r is None:
        return BuildingInfo(property_id=p.property_id, available=False,
                            reason="이 매물 지번은 건축물대장을 조회하지 않았습니다." if key else "주소가 지번 형식이 아니라 조회할 수 없습니다.")
    status = r["match_status"]
    if status != "matched":
        return BuildingInfo(property_id=p.property_id, available=False, match_status=status, reason=REASONS.get(status),
                            fetched_at=r.get("fetched_at") or None)
    day = r.get("use_apr_day") or ""
    date = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else None
    notes = ["광고 주소(지번) 기준 대장 정보입니다. 광고 주소가 실제 건물과 다르면 다른 건물 정보일 수 있습니다."]
    main = r.get("main_purps") or None
    if main and "근린생활시설" in main:
        notes.append("대장상 주용도가 근린생활시설입니다. 주거용으로 계약·전입이 가능한지 계약 전에 확인해 보세요.")
    return BuildingInfo(
        property_id=p.property_id, available=True, match_status=status,
        building_name=r.get("bld_nm") or None, main_purpose=main, detail_purpose=r.get("etc_purps") or None,
        use_approval_date=date, use_approval_year=int(day[:4]) if date else None,
        floors_above=_i(r.get("grnd_flr_cnt")), floors_below=_i(r.get("ugrnd_flr_cnt")),
        elevators=_i(r.get("elevator_cnt")), parking=_i(r.get("parking_cnt")), households=_i(r.get("hhld_cnt")),
        structure=r.get("strct") or None, total_area_m2=_f(r.get("tot_area_m2")),
        fetched_at=r.get("fetched_at") or None, notes=notes,
    )
