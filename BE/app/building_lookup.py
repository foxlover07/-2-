"""목록에 없는 학교 건물 → 카카오 장소 검색으로 찾아 건물 기준표에 추가 (2026-11-16 사용자 결정).

시간표 사진에서 읽은 강의실 표기(예: '정보통신관 604')가 건물 목록에 없을 때 쓴다.
- 검색: '<대학> <건물>' 을 학교 중심(DB/ref 건물 좌표 평균)에서 RADIUS_M 안으로 카카오 키워드 검색.
- 채택: 장소 이름에 대학 이름이 있고, 대학 이름을 뗀 나머지가 찾는 건물 이름과 같거나 그 이름으로 끝날 때만.
  학과·연구소·식당 같은 장소는 (찾는 이름에 그 낱말이 없으면) 버린다. 맞는 게 없으면 추가하지 않는다 (만들어내지 않음).
- 저장: DB/ref/auto_buildings.csv (building_walks 가 *_buildings.csv 로 함께 읽는다. coord_source=kakao_keyword_auto,
  entrance_verified=false, 카카오 POI 대표 지점 · 출입구 아님 · 팀 확인 필요). 못 찾은 이름도 기록해 다시 부르지 않는다.
- 상한: 하루 LOOKUP_DAILY_LIMIT 회 (BUILDING_LOOKUP_DAILY_LIMIT). 도보 경로 한도와 따로 센다 (장소 검색 API).
  BUILDING_LOOKUP_KAKAO=off 이면 부르지 않는다.
도보시간은 여기서 계산하지 않는다 — 그 건물을 고르고 추천받을 때 building_walks 가 그때 계산한다.
"""

from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app import frequent_places as fp
from app.config import _read

RADIUS_M = 1200
LOOKUP_DAILY_LIMIT = 100
AUTO_FILE = "auto_buildings.csv"
FIELDS = ["destination_id", "university", "csv_spot_name", "point_role", "building_type", "kakao_place_name",
          "kakao_place_id", "latitude", "longitude", "coord_source", "checked_at", "entrance_verified", "note"]
# 대학 이름 표기 (장소 이름 앞부분). 긴 것부터 뗀다
UNI_NAMES = {
    "고려대학교": ("고려대학교서울캠퍼스", "고려대학교", "고려대"),
    "이화여자대학교": ("이화여자대학교", "이화여대"),
    "서강대학교": ("서강대학교", "서강대"),
    "서울과학기술대학교": ("서울과학기술대학교", "서울과기대"),
}
# 건물이 아닌 장소 (찾는 이름에 이 낱말이 있으면 허용)
NOT_BUILDING = ("학과", "학부", "연구소", "연구원", "센터", "사무실", "행정실", "카페", "식당", "편의점", "은행", "ATM",
                "주차장", "정문", "후문", "약국", "서점", "우체국", "동아리", "매점", "문구", "복사", "병원")

_lock = threading.Lock()


@dataclass
class Lookup:
    status: str          # added | known | not_found | ambiguous | limit | unavailable | error | no_campus | skipped
    name: str | None = None   # 표준 이름 (added·known)
    place_name: str | None = None


def _norm(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", s).upper()


def _strip_uni(place_name: str, university: str) -> str | None:
    """'고려대학교 정보통신관' → '정보통신관'. 대학 이름이 없으면 None (캠퍼스 밖 장소일 수 있음)."""
    s = place_name.strip()
    for p in UNI_NAMES.get(university, (university,)):
        if s.replace(" ", "").startswith(p):
            # 공백 차이를 무시하고 앞부분을 뗀다
            cut, i = 0, 0
            while i < len(p) and cut < len(s):
                if s[cut] != " ":
                    i += 1
                cut += 1
            rest = s[cut:].strip()
            for q in ("서울캠퍼스",):
                if rest.startswith(q):
                    rest = rest[len(q):].strip()
            return rest or None
    return None


def _paths() -> tuple[Path, Path | None, Path | None]:
    return (fp.REF_DIR / AUTO_FILE,
            fp._path("BUILDING_LOOKUP_LOG_PATH", "../DB/staging/building_lookup_log.json"),
            fp._path("BUILDING_LOOKUP_CALL_LOG_PATH", "../DB/staging/building_lookup_calls.json"))


def _limit() -> int:
    try:
        return int(_read("BUILDING_LOOKUP_DAILY_LIMIT") or LOOKUP_DAILY_LIMIT)
    except ValueError:
        return LOOKUP_DAILY_LIMIT


class _State:
    def __init__(self) -> None:
        auto, log, calls = _paths()
        self.auto_path = auto
        self.log_path = log
        self.log: dict[str, dict[str, Any]] = {}
        if log and log.exists():
            try:
                self.log = json.loads(log.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.log = {}
        self.budget = fp.CallBudget(calls, _limit())
        self.client = None
        if (_read("BUILDING_LOOKUP_KAKAO") or "on").strip().lower() != "off":
            self.client = fp.services().client  # 카카오 키가 없으면 None

    def remember(self, key: str, value: dict[str, Any]) -> None:
        self.log[key] = value
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.log_path.write_text(json.dumps(self.log, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass


_state: _State | None = None


def state() -> _State:
    global _state
    with _lock:
        if _state is None:
            _state = _State()
        return _state


def set_state(s: _State | None) -> None:
    """테스트용."""
    global _state
    with _lock:
        _state = s


def _pick(docs: list[dict[str, Any]], name: str, university: str) -> dict[str, Any] | None:
    want = _norm(name)
    allowed = [w for w in NOT_BUILDING if w not in name]
    for d in docs:
        rest = _strip_uni(str(d.get("place_name", "")), university)
        if not rest or any(w in rest for w in allowed):
            continue
        r = _norm(rest)
        if r == want or (len(want) >= 3 and r.endswith(want)):
            return d
    return None


def _append(row: dict[str, str], path: Path) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def find_building(university: str, name: str, st: _State | None = None) -> Lookup:
    """건물 이름 하나를 카카오에서 찾아 기준표에 추가한다. 이미 찾아본 이름은 다시 부르지 않는다."""
    from app import building_walks

    name = re.sub(r"\s+", " ", name).strip(" '\"()[]")
    if len(_norm(name)) < 2:
        return Lookup("skipped")
    st = st or state()
    key = f"{university}|{_norm(name)}"
    seen = st.log.get(key)
    if seen:
        return Lookup(seen["status"] if seen["status"] != "added" else "known", seen.get("name"), seen.get("place_name"))
    if st.client is None:
        return Lookup("unavailable")
    center = fp.campus_center(university)
    if center is None:
        return Lookup("no_campus")
    if not st.budget.take():
        return Lookup("limit")
    q = f"{university} {name}"
    try:
        res = st.client.search_places_near(center=center, radius_m=RADIUS_M, query=q, size=10, sort="accuracy")
    except Exception:  # noqa: BLE001 — 네트워크·HTTP 오류는 이번엔 '못 찾음'으로 두고 기록하지 않는다
        return Lookup("error")
    d = _pick(res.get("documents") or [], name, university)
    today = datetime.now(fp.KST).strftime("%Y-%m-%d")
    if d is None:
        st.remember(key, {"status": "not_found", "query": q, "checked_at": today})
        return Lookup("not_found")
    std = _strip_uni(str(d["place_name"]), university) or name
    with _lock:
        if std not in building_walks.catalog_names(university):
            _append({
                "destination_id": f"AUTO_{d.get('id')}", "university": university, "csv_spot_name": "",
                "point_role": "building_poi", "building_type": "auto", "kakao_place_name": d["place_name"],
                "kakao_place_id": str(d.get("id", "")), "latitude": str(d["y"]), "longitude": str(d["x"]),
                "coord_source": "kakao_keyword_auto", "checked_at": today, "entrance_verified": "false",
                "note": f"시간표 표기 '{name}'로 자동 검색. 카카오 POI 대표 지점, 출입구 아님. 팀 확인 필요",
            }, st.auto_path)
            building_walks._catalog.cache_clear()
    st.remember(key, {"status": "added", "name": std, "place_name": d["place_name"], "checked_at": today})
    return Lookup("added", std, d["place_name"])
