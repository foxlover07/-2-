"""매물 주변 생활 편의시설 (상가(상권)정보, 소상공인시장진흥공단).

- 거리는 매물 좌표(지번 대표점) ↔ 업소 좌표의 **직선거리**(m)다. 도보 경로 시간이 아니다.
- 업소는 상가정보에 등록된 업소 기준(기준연월 stdr_ym)이며 실제 영업 여부는 보장하지 않는다.
- 주점·노래방 수는 '주변 상권' 참고 정보다. 소음 수준·안전으로 해석하지 않는다.
- 추천 점수에는 쓰지 않는다 (통합안 엔진은 예산·이동 시간·면적 3요소. 카테고리 추가는 팀 결정 필요).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.config import REPO_ROOT, _read
from app.models import Property

SOURCE = "소상공인시장진흥공단 상가(상권)정보 (공공데이터포털)"
RADII = (300, 500)
# 화면 순서와 묶음. 'context' 는 편의시설이 아니라 상권 참고 정보.
CATEGORY_INFO = {
    "convenience": ("편의점", "daily"), "supermarket": ("슈퍼마켓", "daily"), "laundry": ("세탁소", "daily"),
    "laundromat": ("셀프 빨래방", "daily"), "gym": ("헬스장", "daily"), "pharmacy": ("약국", "health"),
    "clinic": ("의원", "health"),
    "bar": ("주점", "context"), "karaoke": ("노래방", "context"),
}


@dataclass(frozen=True)
class Store:
    category: str
    name: str
    address: str
    lat: float
    lon: float
    stdr_ym: str


def _store_paths() -> list[Path]:
    override = (_read("STORES_PATH") or "").strip()
    if override:
        return [Path(p.strip()) if Path(p.strip()).is_absolute() else (REPO_ROOT / "BE" / p.strip()).resolve()
                for p in override.split(",")]
    # 업종을 나눠 수집할 수 있다 (예: 헬스장만 추가 수집) → 모든 stores_*.csv 를 읽되 업종마다 가장 최근 파일만 쓴다.
    return sorted((REPO_ROOT / "DB" / "staging").glob("stores_*.csv"))


def _categories_in(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig") as f:
        return {r["category"] for r in csv.DictReader(f)}


@lru_cache
def load_stores() -> tuple[Store, ...]:
    out: list[Store] = []
    taken: set[str] = set()   # 이미 더 최근 파일에서 읽은 업종
    for path in reversed(_store_paths()):
        if not path.exists():
            continue
        cats = _categories_in(path) - taken
        taken |= cats
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            if r.get("category") not in cats:
                continue
            try:
                out.append(Store(r["category"], (r["name"] + (" " + r["branch"] if r["branch"] else "")).strip(),
                                 r["rdnm_adr"] or r["lno_adr"], float(r["lat"]), float(r["lon"]), r["stdr_ym"]))
            except (KeyError, ValueError):
                continue
    return tuple(out)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class Nearest(BaseModel):
    name: str
    address: str
    distance_m: int


class CategoryStat(BaseModel):
    category: str
    label: str
    group: str               # daily | health | context
    nearest: Nearest | None  # 최대 반경 안에서 가장 가까운 곳 (없으면 null)
    counts: dict[str, int]   # "300" → 반경 300m 안 업소 수


class AmenitiesResult(BaseModel):
    property_id: str
    available: bool
    reason: str | None = None
    radii_m: list[int] = list(RADII)
    categories: list[CategoryStat] = []
    stdr_ym: list[str] = []
    source: str = SOURCE
    notes: list[str] = []


def amenities_for(p: Property, stores: tuple[Store, ...] | None = None) -> AmenitiesResult:
    stores = load_stores() if stores is None else stores
    if not stores:
        return AmenitiesResult(property_id=p.property_id, available=False, reason="상가정보 데이터가 없습니다 (수집 전).")
    if p.latitude is None or p.longitude is None:
        return AmenitiesResult(property_id=p.property_id, available=False,
                               reason="매물 좌표가 없어 거리를 계산할 수 없습니다 (주소를 좌표로 확정하지 못함).")
    max_r = max(RADII)
    # 위도·경도 범위로 먼저 거른 뒤 거리 계산
    dlat = max_r / 111_000
    dlon = max_r / (111_000 * math.cos(math.radians(p.latitude)))
    near = [(s, distance_m(p.latitude, p.longitude, s.lat, s.lon)) for s in stores
            if abs(s.lat - p.latitude) <= dlat and abs(s.lon - p.longitude) <= dlon]
    cats: list[CategoryStat] = []
    for cat, (label, group) in CATEGORY_INFO.items():
        inside = sorted(((s, d) for s, d in near if s.category == cat and d <= max_r), key=lambda x: x[1])
        cats.append(CategoryStat(
            category=cat, label=label, group=group,
            nearest=Nearest(name=inside[0][0].name, address=inside[0][0].address, distance_m=round(inside[0][1])) if inside else None,
            counts={str(r): sum(1 for _, d in inside if d <= r) for r in RADII},
        ))
    return AmenitiesResult(
        property_id=p.property_id, available=True, categories=cats,
        stdr_ym=sorted({s.stdr_ym for s in stores if s.stdr_ym}),
        notes=["거리는 직선거리(m)이며 도보 경로 시간이 아닙니다.",
               "상가정보에 등록된 업소 기준이라 실제 영업 여부와 다를 수 있습니다.",
               "업종은 상가정보 분류를 그대로 따릅니다 (예: '슈퍼마켓'에 무인 아이스크림 가게·드럭스토어가 들어 있을 수 있음).",
               "주점·노래방 수는 주변 상권 참고 정보이며 소음 수준이나 안전을 뜻하지 않습니다."],
    )
