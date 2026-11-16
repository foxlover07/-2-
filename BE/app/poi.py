"""매물 주변 교통·공원 (지하철역 · 버스정류장 · 도시공원) 직선거리 — 참고 정보.

- 데이터: DB/staging/poi_*.csv 중 가장 최근 파일 (pipeline.collect_poi, POI_PATH 로 바꿀 수 있음).
- 거리는 매물 좌표(지번 대표점) ↔ 역사·정류장·공원 대표점의 **직선거리**(m)다. 도보 경로·출입구 기준이 아니다.
- 지하철은 같은 이름의 역(노선별 행)을 한 역으로 묶어 센다. 버스정류장은 길 양쪽 정류장을 따로 센다 (원본 그대로).
- 추천 점수에는 쓰지 않는다 (엔진 3요소는 예산·이동 시간·면적). 값이 없으면 None (0 으로 채우지 않음).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.amenities import distance_m
from app.config import REPO_ROOT, _read
from app.models import Property

# kind → (이름, 개수를 세는 반경들). 통합 데이터셋에 반경마다 count_<r>m_<kind> 열 (500m 는 모든 종류에 있음).
KINDS: dict[str, tuple[str, tuple[int, ...]]] = {
    "subway": ("지하철역", (500, 1000)),
    "bus_stop": ("버스정류장", (300, 500)),
    "park": ("공원", (500, 1000)),
}
SEARCH_MAX_M = 3000   # 이 안에 없으면 nearest = None


@dataclass(frozen=True)
class Poi:
    kind: str
    name: str
    detail: str        # 지하철: 노선명 / 버스: 단축번호 / 공원: 공원구분
    lat: float
    lon: float
    area_m2: float | None
    ref_date: str


def _path() -> Path | None:
    override = (_read("POI_PATH") or "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (REPO_ROOT / "BE" / p).resolve()
    files = sorted((REPO_ROOT / "DB" / "staging").glob("poi_*.csv"))
    return files[-1] if files else None


@lru_cache
def load_pois() -> tuple[Poi, ...]:
    p = _path()
    if p is None or not p.exists():
        return ()
    out = []
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append(Poi(r["kind"], r["name"], r.get("detail") or "", float(r["lat"]), float(r["lon"]),
                               float(r["area_m2"]) if r.get("area_m2") else None, r.get("ref_date") or ""))
            except (KeyError, ValueError):
                continue
    return tuple(out)


class NearestPoi(BaseModel):
    name: str
    detail: str | None = None     # 지하철: 노선들(· 구분) / 공원: 공원구분
    distance_m: int
    area_m2: float | None = None


class PoiStat(BaseModel):
    kind: str
    label: str
    nearest: NearestPoi | None
    counts: dict[str, int]        # "500" → 반경 500m 안 개수


class PoiResult(BaseModel):
    property_id: str
    available: bool
    reason: str | None = None
    kinds: list[PoiStat] = []
    notes: list[str] = []


STATION_MERGE_M = 300   # 같은 이름이고 이 거리 안이면 한 역 (노선별 행). 2호선 '신촌'과 경의중앙선 '신촌역'처럼 멀면 따로.


def group_stations(rows: list[tuple[Poi, float]]) -> list[tuple[str, float, list[str]]]:
    """지하철 행(거리순) → [(역 이름, 가장 가까운 거리, 노선들)] 거리순."""
    groups: list[tuple[Poi, float, list[str]]] = []   # (대표 행, 거리, 노선)
    for x, d in rows:
        for i, (rep, gd, lines) in enumerate(groups):
            if rep.name == x.name and distance_m(rep.lat, rep.lon, x.lat, x.lon) <= STATION_MERGE_M:
                if x.detail and x.detail not in lines:
                    lines.append(x.detail)
                groups[i] = (rep, min(gd, d), lines)
                break
        else:
            groups.append((x, d, [x.detail] if x.detail else []))
    return sorted(((rep.name, d, lines) for rep, d, lines in groups), key=lambda t: t[1])


def nearby_pois(p: Property, pois: tuple[Poi, ...] | None = None) -> PoiResult:
    pois = load_pois() if pois is None else pois
    if not pois:
        return PoiResult(property_id=p.property_id, available=False, reason="교통·공원 데이터가 없습니다 (pipeline.collect_poi 실행 전).")
    if p.latitude is None or p.longitude is None:
        return PoiResult(property_id=p.property_id, available=False, reason="매물 좌표가 없어 거리를 계산할 수 없습니다.")
    dlat = SEARCH_MAX_M / 111_000
    dlon = SEARCH_MAX_M / (111_000 * math.cos(math.radians(p.latitude)))
    near = [(x, distance_m(p.latitude, p.longitude, x.lat, x.lon)) for x in pois
            if abs(x.lat - p.latitude) <= dlat and abs(x.lon - p.longitude) <= dlon]
    stats = []
    for kind, (label, radii) in KINDS.items():
        inside = sorted(((x, d) for x, d in near if x.kind == kind and d <= SEARCH_MAX_M), key=lambda t: t[1])
        if kind == "subway":
            stations = group_stations(inside)
            nearest = (NearestPoi(name=stations[0][0], detail=" · ".join(stations[0][2]) or None,
                                  distance_m=round(stations[0][1])) if stations else None)
            counts = {str(r): sum(1 for _, d, _ in stations if d <= r) for r in radii}
        else:
            nearest = (NearestPoi(name=inside[0][0].name, detail=inside[0][0].detail or None, distance_m=round(inside[0][1]),
                                  area_m2=inside[0][0].area_m2) if inside else None)
            counts = {str(r): sum(1 for _, d in inside if d <= r) for r in radii}
        stats.append(PoiStat(kind=kind, label=label, nearest=nearest, counts=counts))
    return PoiResult(property_id=p.property_id, available=True, kinds=stats, notes=[
        "거리는 직선거리(m)이며 도보 경로가 아닙니다. 역은 역사 대표점, 공원은 공원 대표점 기준(출입구 아님).",
        "지하철역 데이터는 2020~2021년 기준이라 그 뒤 개통한 역·노선은 빠져 있을 수 있습니다.",
    ])
