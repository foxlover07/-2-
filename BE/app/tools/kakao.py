"""Kakao Local / 도보 경로 REST 클라이언트.

공식 문서 (2026-11-06 확인): https://developers.kakao.com/docs/ko/kakaomap/rest-api
- 주소 검색  GET https://dapi.kakao.com/v2/local/search/address.json   (query, analyze_type=similar|exact)
- 키워드 검색 GET https://dapi.kakao.com/v2/local/search/keyword.json
- 도보 경로  GET https://dapi.kakao.com/v2/routing/walk
  start_x/end_x = 경도, start_y/end_y = 위도 (WGS84 기본)
  route_mode = BROAD_FIRST(기본) | SHORTEST | ACCESSIBLE
  status == "OK" 일 때만 route.properties.totalDistance(m), totalTime(s) 가 있다.
- 무료 쿼터: 도보 경로 1,000건/일 (초과 시 429, 유료 10원/건). 주소 검색 100,000건/일.

원칙: 실패를 0분·직선거리로 대체하지 않는다. 키 값은 로그·예외에 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

BASE = "https://dapi.kakao.com"
ROUTE_MODES = ("BROAD_FIRST", "SHORTEST", "ACCESSIBLE")


@dataclass
class LatLng:
    lat: float
    lng: float


@dataclass
class WalkResult:
    status: str  # 카카오 status (OK, SAME_POINT, ...) 또는 HTTP_<code>
    route_mode: str
    distance_m: int | None = None
    time_s: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == "OK"


class KakaoClient:
    def __init__(self, rest_api_key: str, transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        if not rest_api_key:
            raise ValueError("KAKAO_REST_API_KEY 가 설정되지 않았다")
        self._http = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"KakaoAK {rest_api_key}"},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict) -> httpx.Response:
        return self._http.get(path, params=params)

    def search_address(self, query: str, exact: bool = True) -> dict:
        res = self._get("/v2/local/search/address.json",
                        {"query": query, "analyze_type": "exact" if exact else "similar"})
        res.raise_for_status()
        return res.json()

    def search_keyword(self, query: str, size: int = 5, page: int = 1, rect: str | None = None) -> dict:
        """rect = '좌하단경도,좌하단위도,우상단경도,우상단위도' (선택)."""
        params = {"query": query, "size": size, "page": page}
        if rect:
            params["rect"] = rect
        res = self._get("/v2/local/search/keyword.json", params)
        res.raise_for_status()
        return res.json()

    def search_places_near(
        self,
        *,
        center: LatLng,
        radius_m: int,
        query: str | None = None,
        category_group_code: str | None = None,
        page: int = 1,
        size: int = 15,
        sort: str = "distance",
    ) -> dict:
        """반경 안 장소 검색 (추천 제외 조건 도구용). query 가 있으면 키워드 검색, 없으면 카테고리 검색.

        x = 경도, y = 위도, radius 0~20000m. 문서마다 distance(m, 문자열)가 붙는다.
        """
        if not query and not category_group_code:
            raise ValueError("query 또는 category_group_code 가 필요하다")
        params: dict = {"x": center.lng, "y": center.lat, "radius": radius_m, "page": page, "size": size, "sort": sort}
        if category_group_code:
            params["category_group_code"] = category_group_code
        if query:
            params["query"] = query
            path = "/v2/local/search/keyword.json"
        else:
            path = "/v2/local/search/category.json"
        res = self._get(path, params)
        res.raise_for_status()
        return res.json()

    def walk(self, start: LatLng, end: LatLng, route_mode: str = "BROAD_FIRST") -> WalkResult:
        if route_mode not in ROUTE_MODES:
            raise ValueError(f"route_mode 는 {ROUTE_MODES} 중 하나")
        res = self._get("/v2/routing/walk", {
            "start_x": start.lng, "start_y": start.lat,
            "end_x": end.lng, "end_y": end.lat,
            "route_mode": route_mode,
        })
        try:
            body = res.json()
        except ValueError:
            body = {"_text": res.text[:500]}
        if res.status_code != 200:
            return WalkResult(status=f"HTTP_{res.status_code}", route_mode=route_mode, raw=body)
        status = body.get("status", "MISSING_STATUS")
        if status != "OK":
            return WalkResult(status=status, route_mode=route_mode, raw=body)
        props = body["route"]["properties"]
        return WalkResult(status="OK", route_mode=route_mode,
                          distance_m=props["totalDistance"], time_s=props["totalTime"], raw=body)


def _norm_addr(s: str) -> str:
    s = " ".join(s.split())
    for long, short in (("서울특별시 ", "서울 "), ("서울시 ", "서울 ")):
        if s.startswith(long):
            s = short + s[len(long):]
    return s


def single_exact_match(address_response: dict, query: str | None = None) -> tuple[LatLng, dict] | None:
    """좌표를 하나로 확정할 수 있을 때만 돌려준다. 임의로 고르지 않는다.

    - 결과가 1건이면 그것.
    - 여러 건이면 address_name 이 query 와 (시·도 표기만 맞춘 뒤) 완전히 같은 문서가 정확히 1건일 때만 그것.
    - 그 밖(0건, 판별 불가)은 None.
    """
    docs = address_response.get("documents", [])
    if len(docs) > 1 and query:
        docs = [d for d in docs if _norm_addr(d["address_name"]) == _norm_addr(query)]
    if len(docs) != 1:
        return None
    d = docs[0]
    return LatLng(lat=float(d["y"]), lng=float(d["x"])), d
