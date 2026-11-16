"""자주 가는 곳 (알바·헬스장·역 등) → 매물에서 그곳까지 도보시간 (2026-11-16 사용자 결정, 챗봇 전용).

흐름
1. 장소 찾기: 카카오 키워드 검색을 학교 중심 반경 SEARCH_RADIUS_M 안에서 한다 (1회).
   - 첫 결과 이름이 사용자 표현을 담고 있으면 그것으로 정한다 (확인 질문 없이 적용, 이름·주소를 보여 준다).
   - 아니면 후보 몇 개를 보여 주고 다시 묻는다 (임의로 고르지 않는다).
   - 반경 안에 없으면 반경 없이 한 번 더 찾아(1회) '도보 범위 밖'인지 '못 찾음'인지 구분한다.
2. 도보시간: 추천 상위 TOP_N 매물만 카카오 도보 경로로 계산한다 (장소 1곳에 최대 TOP_N 회).
   - 결과는 매물 ID × 카카오 장소 ID 로 파일에 저장해 다시 쓴다 (실패도 저장해 같은 호출을 반복하지 않는다).
   - 실패·좌표 없음·하루 상한 초과는 None (0분·직선거리로 바꾸지 않는다).
3. 하루 호출 상한 DAILY_LIMIT (장소 검색 + 도보 경로 합계, 기존 경로 수집과 별도로 세는 이 기능의 몫).

설정 (BE/.env.local)
- FREQUENT_PLACES_KAKAO = on(기본) | off  — off 면 카카오를 부르지 않는다 (테스트)
- FREQUENT_WALK_CACHE_PATH  기본 ../DB/staging/frequent_walk_cache.csv (커밋 안 함). 빈 값 = 메모리만
- FREQUENT_CALL_LOG_PATH    기본 ../DB/staging/frequent_kakao_calls.json (날짜별 호출 수). 빈 값 = 메모리만
- FREQUENT_DAILY_CALL_LIMIT 기본 200
"""

from __future__ import annotations

import csv
import json
import os
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Protocol

from pydantic import BaseModel, Field

from app.config import REPO_ROOT, _read
from app.tools.kakao import KakaoClient, LatLng, WalkResult

KST = timezone(timedelta(hours=9))
SEARCH_RADIUS_M = 3000       # 학교 중심에서 이 반경 안의 장소만 도보 계산 (사용자 결정 2026-11-16)
TOP_N = 20                   # 추천 상위 몇 개 매물만 도보 경로를 계산하는지 (사용자 결정 2026-11-16)
DEFAULT_DAILY_LIMIT = 200    # 이 기능의 하루 카카오 호출 상한 (사용자 결정 2026-11-16)
MAX_PLACES = 3               # 저장하는 자주 가는 곳 수
ROUTE_MODE = "BROAD_FIRST"   # 기존 경로 수집(pipeline)과 같은 값
CANDIDATES_SHOWN = 3


def _ref_dir() -> Path:
    """건물 기준표 폴더. BUILDING_REF_DIR 로 바꿀 수 있다 (배포: Render Secret Files 의 /etc/secrets). 상대 경로는 BE 기준."""
    v = (os.getenv("BUILDING_REF_DIR") or _read("BUILDING_REF_DIR") or "").strip()
    if not v:
        return REPO_ROOT / "DB" / "ref"
    p = Path(v)
    return p if p.is_absolute() else (REPO_ROOT / "BE" / p).resolve()


REF_DIR = _ref_dir()
CACHE_FIELDS = ["property_id", "place_id", "place_name", "status", "walk_seconds", "distance_m", "route_mode", "checked_at"]


class FrequentPlace(BaseModel):
    """카카오에서 찾은 자주 가는 곳 (프로필에 저장)."""

    name: str = Field(description="사용자가 말한 표현 그대로 (예: 알바하는 곳 신촌역)")
    visits_per_week: int = Field(ge=1, le=14)
    place_id: str
    place_name: str
    address: str | None = None
    latitude: float
    longitude: float
    campus_distance_m: int | None = None  # 학교 중심에서 직선거리 (카카오 응답 distance)


class _Client(Protocol):
    def search_places_near(self, **kw: Any) -> dict: ...
    def search_keyword(self, query: str, size: int = 5, page: int = 1, rect: str | None = None) -> dict: ...
    def walk(self, start: LatLng, end: LatLng, route_mode: str = ...) -> WalkResult: ...


# ---------------------------------------------------------------- 설정·상태

def _path(env: str, default: str) -> Path | None:
    v = os.getenv(env)
    if v is None:
        v = _read(env)
    if v is None:
        v = default
    if not v.strip():
        return None
    p = Path(v)
    return p if p.is_absolute() else (REPO_ROOT / "BE" / p).resolve()


def daily_limit() -> int:
    try:
        return int(_read("FREQUENT_DAILY_CALL_LIMIT") or DEFAULT_DAILY_LIMIT)
    except ValueError:
        return DEFAULT_DAILY_LIMIT


def _today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


class CallBudget:
    """날짜별 카카오 호출 수 (이 기능 몫). 파일이 없으면 메모리에만 센다."""

    def __init__(self, path: Path | None, limit: int):
        self.path, self.limit = path, limit
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        if path and path.exists():
            try:
                self._counts = {k: int(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
            except (ValueError, OSError):
                self._counts = {}

    def used(self) -> int:
        return self._counts.get(_today(), 0)

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def exhaust(self) -> None:
        """카카오가 '한도 초과'를 돌려주면 오늘은 더 부르지 않는다."""
        with self._lock:
            self._counts = {_today(): max(self.limit, self._counts.get(_today(), 0))}
            if self.path:
                try:
                    self.path.write_text(json.dumps(self._counts), encoding="utf-8")
                except OSError:
                    pass

    def take(self) -> bool:
        """호출 1회를 쓸 수 있으면 세고 True."""
        with self._lock:
            day = _today()
            if self._counts.get(day, 0) >= self.limit:
                return False
            self._counts = {day: self._counts.get(day, 0) + 1}  # 지난 날짜는 버린다
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_text(json.dumps(self._counts), encoding="utf-8")
                except OSError:
                    pass
            return True


class WalkCache:
    """매물 ID × 장소 ID → 도보 결과 (성공·실패 모두). CSV append-only."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, str]] = {}
        if path and path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    self._rows[(row["property_id"], row["place_id"])] = row

    def get(self, property_id: str, place_id: str) -> dict[str, str] | None:
        return self._rows.get((property_id, place_id))

    def put(self, row: dict[str, str]) -> None:
        with self._lock:
            self._rows[(row["property_id"], row["place_id"])] = row
            if not self.path:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                new = not self.path.exists()
                with self.path.open("a", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
                    if new:
                        w.writeheader()
                    w.writerow({k: row.get(k, "") for k in CACHE_FIELDS})
            except OSError:
                pass


@dataclass
class Services:
    client: _Client | None
    budget: CallBudget
    cache: WalkCache


_services: Services | None = None
_services_lock = threading.Lock()


def _default_client() -> _Client | None:
    if (_read("FREQUENT_PLACES_KAKAO") or "on").strip().lower() == "off":
        return None
    key = _read("KAKAO_REST_API_KEY")
    return KakaoClient(key) if key else None


def services() -> Services:
    global _services
    with _services_lock:
        if _services is None:
            _services = Services(
                client=_default_client(),
                budget=CallBudget(_path("FREQUENT_CALL_LOG_PATH", "../DB/staging/frequent_kakao_calls.json"), daily_limit()),
                cache=WalkCache(_path("FREQUENT_WALK_CACHE_PATH", "../DB/staging/frequent_walk_cache.csv")),
            )
        return _services


def set_services(s: Services | None) -> None:
    """테스트용: 가짜 카카오·메모리 저장소로 바꾼다 (None 이면 다음에 설정에서 다시 만든다)."""
    global _services
    with _services_lock:
        _services = s


# ---------------------------------------------------------------- 학교 중심

@lru_cache(maxsize=8)
def campus_center(university: str) -> LatLng | None:
    """DB/ref/*_buildings.csv 의 그 대학 건물 좌표 평균 (카카오 POI 대표 지점 기준, 출입구 아님)."""
    lats: list[float] = []
    lngs: list[float] = []
    for f in sorted(REF_DIR.glob("*_buildings.csv")):
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("university") != university:
                    continue
                try:
                    lats.append(float(row["latitude"]))
                    lngs.append(float(row["longitude"]))
                except (KeyError, TypeError, ValueError):
                    continue
    if not lats:
        return None
    return LatLng(lat=sum(lats) / len(lats), lng=sum(lngs) / len(lngs))


# ---------------------------------------------------------------- 1. 장소 찾기

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if ch.isalnum())


@dataclass
class PlaceLookup:
    status: str  # ok | ambiguous | not_found | out_of_range | unavailable | limit | error | no_campus
    place: FrequentPlace | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)  # ambiguous 일 때 (이름·주소·거리)
    query: str = ""


def _doc_view(d: dict[str, Any]) -> dict[str, Any]:
    dist = d.get("distance")
    return {
        "place_name": d.get("place_name"),
        "address": d.get("road_address_name") or d.get("address_name") or None,
        "distance_m": int(dist) if str(dist or "").isdigit() else None,
    }


def resolve_place(query: str, name: str, visits_per_week: int, university: str,
                  svc: Services | None = None) -> PlaceLookup:
    """사용자 표현 → 카카오 장소 하나. query 는 검색어(장소 부분), name 은 저장할 표현."""
    svc = svc or services()
    q = query.strip()
    if svc.client is None:
        return PlaceLookup("unavailable", query=q)
    center = campus_center(university)
    if center is None:
        return PlaceLookup("no_campus", query=q)
    if not svc.budget.take():
        return PlaceLookup("limit", query=q)
    try:
        res = svc.client.search_places_near(center=center, radius_m=SEARCH_RADIUS_M, query=q, size=5, sort="accuracy")
    except Exception:  # noqa: BLE001 — 네트워크·HTTP 오류는 '확인 못 함'으로
        return PlaceLookup("error", query=q)
    docs = res.get("documents") or []
    if not docs:
        if not svc.budget.take():
            return PlaceLookup("not_found", query=q)
        try:
            far = svc.client.search_keyword(q, size=1).get("documents") or []
        except Exception:  # noqa: BLE001
            far = []
        return PlaceLookup("out_of_range" if far else "not_found", query=q,
                           candidates=[_doc_view(d) for d in far])
    top = docs[0]
    nq, nt = _norm(q), _norm(str(top.get("place_name", "")))
    if not (nq and (nq in nt or nt in nq)):
        return PlaceLookup("ambiguous", query=q, candidates=[_doc_view(d) for d in docs[:CANDIDATES_SHOWN]])
    try:
        lat, lng = float(top["y"]), float(top["x"])
    except (KeyError, TypeError, ValueError):
        return PlaceLookup("error", query=q)
    view = _doc_view(top)
    return PlaceLookup("ok", query=q, place=FrequentPlace(
        name=name.strip() or q, visits_per_week=visits_per_week, place_id=str(top.get("id")),
        place_name=str(top.get("place_name")), address=view["address"], latitude=lat, longitude=lng,
        campus_distance_m=view["distance_m"],
    ))


def lookup_note(lk: PlaceLookup) -> str:
    """찾기 결과 안내 문장 (ok 는 lead 문장에서 따로 만든다)."""
    q = lk.query
    if lk.status == "ambiguous":
        c = " / ".join(f"{x['place_name']}({x['address'] or '주소 없음'})" for x in lk.candidates)
        return f"'{q}'(으)로 찾은 곳이 여럿이라 정하지 못했어요: {c}. 정확한 이름을 알려 주세요."
    if lk.status == "out_of_range":
        return (f"'{q}'은(는) 학교에서 {SEARCH_RADIUS_M // 1000}km 밖이라 도보 계산 범위가 아니에요 "
                "(대중교통 경로는 아직 지원하지 않아요).")
    if lk.status == "not_found":
        return f"'{q}'을(를) 카카오 장소 검색에서 찾지 못했어요. 가게·역 이름을 정확히 알려 주세요."
    if lk.status == "limit":
        return "오늘 장소 검색·도보 계산 한도를 다 써서 자주 가는 곳을 반영하지 못했어요. 내일 다시 시도해 주세요."
    if lk.status == "unavailable":
        return "서버에 카카오 키가 없어 자주 가는 곳을 찾을 수 없어요."
    if lk.status == "no_campus":
        return "이 학교의 기준 위치가 없어 자주 가는 곳을 찾을 수 없어요."
    return f"'{q}'을(를) 찾는 중 오류가 나서 반영하지 못했어요."


# ---------------------------------------------------------------- 2. 도보시간

def is_quota_error(r: WalkResult) -> bool:
    """카카오 한도 초과 응답 (2026-11-16 실측: HTTP 400, code -10, 'API limit has been exceeded.')."""
    if r.status == "HTTP_429":
        return True
    raw = r.raw or {}
    return r.status.startswith("HTTP_") and (raw.get("code") == -10 or "limit" in str(raw.get("msg", "")).lower())


@dataclass
class WalkStats:
    computed: int = 0     # 이번에 카카오로 계산
    cached: int = 0       # 저장된 결과 사용
    failed: int = 0       # 경로 실패 (status != OK)
    no_coords: int = 0    # 매물 좌표 없음
    limited: int = 0      # 하루 상한으로 계산 못 함

    def add(self, o: "WalkStats") -> None:
        for k in ("computed", "cached", "failed", "no_coords", "limited"):
            setattr(self, k, getattr(self, k) + getattr(o, k))


def _minutes(row: dict[str, str]) -> float | None:
    if row.get("status") != "OK":
        return None
    try:
        return round(int(row["walk_seconds"]) / 60, 1)
    except (KeyError, TypeError, ValueError):
        return None


def walk_minutes(props: Iterable[Any], place: FrequentPlace, svc: Services | None = None) -> tuple[dict[str, float | None], WalkStats]:
    """매물들 → 장소까지 편도 도보 분. 계산 못 한 매물은 None."""
    svc = svc or services()
    out: dict[str, float | None] = {}
    st = WalkStats()
    end = LatLng(lat=place.latitude, lng=place.longitude)
    for p in props:
        pid = p.property_id
        row = svc.cache.get(pid, place.place_id)
        if row is not None:
            out[pid] = _minutes(row)
            st.cached += 1
            continue
        if p.latitude is None or p.longitude is None:
            out[pid] = None
            st.no_coords += 1
            continue
        if svc.client is None or not svc.budget.take():
            out[pid] = None
            st.limited += 1
            continue
        try:
            r = svc.client.walk(LatLng(lat=p.latitude, lng=p.longitude), end, route_mode=ROUTE_MODE)
            status, secs, dist = r.status, r.time_s, r.distance_m
        except Exception:  # noqa: BLE001 — 네트워크 오류는 저장하지 않고 이번만 실패
            out[pid] = None
            st.failed += 1
            continue
        if is_quota_error(r):
            svc.budget.exhaust()
            out[pid] = None
            st.limited += 1
            continue
        if status.startswith("HTTP_"):  # 그 밖의 HTTP 오류도 저장하지 않는다 (다음에 다시 시도)
            out[pid] = None
            st.failed += 1
            continue
        row = {"property_id": pid, "place_id": place.place_id, "place_name": place.place_name, "status": status,
               "walk_seconds": "" if secs is None else str(secs), "distance_m": "" if dist is None else str(dist),
               "route_mode": ROUTE_MODE, "checked_at": datetime.now(KST).isoformat(timespec="seconds")}
        svc.cache.put(row)
        out[pid] = _minutes(row)
        st.computed += 1
        if out[pid] is None:
            st.failed += 1
    return out, st


def weighted_avg(per_place: list[tuple[int, dict[str, float | None]]], ids: Iterable[str]) -> dict[str, float]:
    """방문 횟수 가중 편도 평균. 장소 중 하나라도 값이 없는 매물은 넣지 않는다 (부분 평균은 치우친다)."""
    out: dict[str, float] = {}
    for pid in ids:
        vals = [(w, m.get(pid)) for w, m in per_place]
        if not vals or any(v is None for _, v in vals):
            continue
        total = sum(w for w, _ in vals)
        out[pid] = sum(w * v for w, v in vals) / total  # type: ignore[operator]
    return out
