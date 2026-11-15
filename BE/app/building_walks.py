"""학교 건물 목록(DB/ref) + 고른 건물만 그때 도보시간 계산 (2026-11-16 사용자 결정 'B 방식').

- 건물 목록: DB/ref/*_buildings.csv 의 강의동·도서관 등 (building_type 이 빈 출입구·정문 행은 빼고).
  표준 이름 = csv_spot_name (기존 도보시간 표 이름) 이 있으면 그것, 없으면 카카오 장소 이름에서 대학 이름을 뗀 것.
  좌표는 카카오 POI 대표 지점이다 (출입구 아님, entrance_verified=false).
- 그때 계산: 추천을 요청할 때 고른 건물(학과 건물·시간표)의 도보시간이 없는 매물만, 예산·보증금 필터를 통과한 매물에 한해
  카카오 도보 경로로 계산한다. 결과는 파일에 저장해 다음부터 다시 쓴다 (load_travel_times 가 합쳐 읽는다).
  요청 1번 최대 MAX_PER_REQUEST 회, 하루 DAILY_LIMIT 회 (자주 가는 곳 200회와 따로 센다. 합계 960회 이하).
  실패·상한 초과는 계산하지 않고 그 매물은 엔진이 '도보시간 정보 없음'으로 뺀다 (0분·직선거리로 바꾸지 않는다).

설정 (BE/.env.local)
- BUILDING_WALK_KAKAO = on(기본) | off
- BUILDING_WALK_CACHE_PATH  기본 ../DB/staging/building_walk_cache.csv (커밋 안 함). 빈 값 = 메모리만
- BUILDING_CALL_LOG_PATH    기본 ../DB/staging/building_kakao_calls.json. 빈 값 = 메모리만
- BUILDING_DAILY_CALL_LIMIT 기본 700
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable

from app import frequent_places as fp
from app.buildings import resolve
from app.config import _read
from app.tools.kakao import KakaoClient, LatLng

MAX_PER_REQUEST = 150
DEFAULT_DAILY_LIMIT = 700
KEY_PREFIX = "bldg:"
_UNI_PREFIXES = ("고려대학교서울캠퍼스", "고려대학교", "서울캠퍼스", "서울과학기술대학교", "이화여자대학교", "서강대학교")


@dataclass(frozen=True)
class Building:
    university: str
    name: str            # 표준 이름 (도보시간 표의 building 값)
    destination_id: str
    building_type: str
    latitude: float
    longitude: float


def _std_name(row: dict[str, str]) -> str:
    if row.get("csv_spot_name"):
        return row["csv_spot_name"].strip()
    s = (row.get("kakao_place_name") or "").strip()
    changed = True
    while changed:
        changed = False
        for p in _UNI_PREFIXES:
            if s.startswith(p):
                s, changed = s[len(p):].strip(), True
    return s


@lru_cache(maxsize=1)
def _catalog() -> tuple[Building, ...]:
    out: list[Building] = []
    seen: set[tuple[str, str]] = set()
    for f in sorted(fp.REF_DIR.glob("*_buildings.csv")):
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if not row.get("building_type"):  # 출입구·정문 등 지점
                    continue
                try:
                    lat, lng = float(row["latitude"]), float(row["longitude"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = _std_name(row)
                key = (row["university"], name)
                if not name or key in seen:
                    continue
                seen.add(key)
                out.append(Building(row["university"], name, row["destination_id"], row["building_type"], lat, lng))
    return tuple(out)


def catalog(university: str) -> list[Building]:
    return [b for b in _catalog() if b.university == university]


def catalog_names(university: str) -> list[str]:
    return [b.name for b in catalog(university)]


# ---------------------------------------------------------------- 저장·호출 상한

_services: fp.Services | None = None
_lock = threading.Lock()


def services() -> fp.Services:
    global _services
    with _lock:
        if _services is None:
            on = (_read("BUILDING_WALK_KAKAO") or "on").strip().lower() != "off"
            key = _read("KAKAO_REST_API_KEY") if on else None
            try:
                limit = int(_read("BUILDING_DAILY_CALL_LIMIT") or DEFAULT_DAILY_LIMIT)
            except ValueError:
                limit = DEFAULT_DAILY_LIMIT
            _services = fp.Services(
                client=KakaoClient(key) if key else None,
                budget=fp.CallBudget(fp._path("BUILDING_CALL_LOG_PATH", "../DB/staging/building_kakao_calls.json"), limit),
                cache=fp.WalkCache(fp._path("BUILDING_WALK_CACHE_PATH", "../DB/staging/building_walk_cache.csv")),
            )
        return _services


def set_services(s: fp.Services | None) -> None:
    """테스트용."""
    global _services
    with _lock:
        _services = s


def _key(university: str, name: str) -> str:
    return f"{KEY_PREFIX}{university}:{name}"


def cached_rows() -> Iterable[tuple[str, str, float]]:
    """(property_id, building, walk_min) — 저장된 성공 결과만 (load_travel_times 가 합친다)."""
    for (pid, place_id), row in list(services().cache._rows.items()):
        if not place_id.startswith(KEY_PREFIX) or row.get("status") != "OK":
            continue
        try:
            yield pid, place_id.split(":", 2)[2], round(int(row["walk_seconds"]) / 60, 1)
        except (IndexError, KeyError, TypeError, ValueError):
            continue


# ---------------------------------------------------------------- 그때 계산

@dataclass
class FillResult:
    travel: dict[str, dict[str, float]]
    computed: int = 0
    failed: int = 0
    limited: int = 0          # 요청·하루 상한으로 못 함
    no_coords: int = 0
    buildings: list[str] = field(default_factory=list)  # 계산 대상이 된 건물

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.computed:
            out.append(f"{', '.join(self.buildings)}까지 도보시간이 없던 매물 {self.computed}곳을 지금 카카오 도보 경로로 계산했습니다"
                       " (다음부터는 저장된 값을 씁니다).")
        if self.limited:
            out.append(f"계산 한도(요청 1번 {MAX_PER_REQUEST}곳·하루 상한) 때문에 매물 {self.limited}곳은 도보시간을 계산하지 못해 "
                       "이번 추천에서 빠졌습니다. 다시 요청하면 이어서 계산합니다.")
        if self.failed:
            out.append(f"도보 경로를 찾지 못한 매물 {self.failed}곳은 추천에서 빠졌습니다.")
        if self.no_coords:
            out.append(f"좌표가 없는 매물 {self.no_coords}곳은 고른 건물까지 도보시간을 계산할 수 없어 추천에서 빠졌습니다.")
        return out


def needed_buildings(university: str, names: Iterable[str], travel: dict[str, dict[str, float]]) -> list[Building]:
    """요청한 건물 이름 → 계산할 수 있는(좌표가 있는) 건물."""
    cat = {b.name: b for b in catalog(university)}
    known = sorted(set(cat) | {n for row in travel.values() for n in row})
    out: list[Building] = []
    for n in names:
        std = resolve(n, known)
        if std in cat and cat[std] not in out:
            out.append(cat[std])
    return out


def fill_missing(props: list[Any], university: str, names: Iterable[str], travel: dict[str, dict[str, float]],
                 keep: Any = None, svc: fp.Services | None = None) -> FillResult:
    """고른 건물의 도보시간이 없는 매물만 계산해 travel 에 채운 사본을 돌려준다.

    keep(property) -> bool: 계산할 매물 (예산·보증금 필터 통과). None 이면 전부.
    """
    svc = svc or services()
    travel = {pid: dict(row) for pid, row in travel.items()}
    res = FillResult(travel=travel)
    targets = needed_buildings(university, names, travel)
    calls = 0
    for b in targets:
        end = LatLng(lat=b.latitude, lng=b.longitude)
        key = _key(university, b.name)
        touched = False
        for p in props:
            if p.university != university or b.name in travel.get(p.property_id, {}):
                continue
            if keep is not None and not keep(p):
                continue
            row = svc.cache.get(p.property_id, key)
            if row is None:
                if p.latitude is None or p.longitude is None:
                    res.no_coords += 1
                    continue
                if svc.client is None or calls >= MAX_PER_REQUEST or not svc.budget.take():
                    res.limited += 1
                    continue
                calls += 1
                try:
                    r = svc.client.walk(LatLng(lat=p.latitude, lng=p.longitude), end, route_mode=fp.ROUTE_MODE)
                except Exception:  # noqa: BLE001 — 네트워크 오류는 저장하지 않는다
                    res.failed += 1
                    continue
                if fp.is_quota_error(r):  # 카카오 한도 초과: 저장하지 않고 오늘은 그만 부른다
                    svc.budget.exhaust()
                    res.limited += 1
                    calls = MAX_PER_REQUEST
                    continue
                if r.status.startswith("HTTP_"):  # 그 밖의 HTTP 오류도 저장하지 않는다 (다음에 다시 시도)
                    res.failed += 1
                    continue
                row = {"property_id": p.property_id, "place_id": key, "place_name": b.name, "status": r.status,
                       "walk_seconds": "" if r.time_s is None else str(r.time_s),
                       "distance_m": "" if r.distance_m is None else str(r.distance_m),
                       "route_mode": fp.ROUTE_MODE, "checked_at": datetime.now(fp.KST).isoformat(timespec="seconds")}
                svc.cache.put(row)
                res.computed += 1
                touched = True
            if row.get("status") == "OK" and row.get("walk_seconds"):
                travel.setdefault(p.property_id, {})[b.name] = round(int(row["walk_seconds"]) / 60, 1)
            elif row.get("status") != "OK":
                res.failed += 1
        if touched:
            res.buildings.append(b.name)
    return res


def fill_for_request(props: list[Any], req: Any, travel: dict[str, dict[str, float]]) -> FillResult:
    """추천 요청(RecommendRequest) 기준: 학과 건물 + 시간표 건물, 예산·보증금 필터를 통과할 매물만."""
    from app.scoring import CONFIG, budget_exclusion_reason, deposit_exclusion_reason, monthly_fixed_cost

    def keep(p: Any) -> bool:
        cost = monthly_fixed_cost(p, CONFIG)
        if cost is None or budget_exclusion_reason(cost, req.budget_manwon, CONFIG):
            return False
        return not deposit_exclusion_reason(p.deposit_manwon, req.deposit_budget_manwon, CONFIG)

    names = [*req.resolved_department_buildings(), *(s.building for s in req.timetable)]
    return fill_missing(props, req.university, names, travel, keep=keep)
