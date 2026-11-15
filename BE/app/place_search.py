"""챗봇 제외 조건의 장소 확인을 상가(상권)정보로 한다 (카카오 호출 없음).

app.exclusions.PlaceSearcher 와 같은 모양(카카오 Local 응답 모양)으로 돌려주므로 엔진(ExclusionRunner)은 그대로 쓴다.

- 장소 이름(키워드)·카카오 카테고리 코드 → 상가정보 분류(app.amenities.CATEGORY_INFO)로 바꿔 찾는다.
  표에 없는 장소(예: 도서관, 카페)는 확인할 수 없다 → 계획을 만들 때 unsupported 로 돌린다 (추측해서 빼지 않음).
- 거리는 매물 좌표 ↔ 업소 좌표의 직선거리(m). 업소는 상가정보 등록 기준(실제 영업 여부 보장 안 함).
- 어느 검색기를 쓸지: PLACE_SEARCH = local(기본) | kakao. local 인데 상가정보 파일이 없으면 None (엔진이 '확인 못 함' 안내).
"""

from __future__ import annotations

import math
from typing import Any

from app.amenities import CATEGORY_INFO, Store, distance_m, load_stores
from app.config import _read

# 사용자 표현 → 상가정보 분류. ⚠️ 상가정보 분류를 따르므로 '슈퍼마켓'에 무인 아이스크림 가게 등이 섞일 수 있다.
KEYWORDS: dict[str, str] = {
    "편의점": "convenience",
    "슈퍼": "supermarket", "슈퍼마켓": "supermarket", "마트": "supermarket",
    "세탁소": "laundry",
    "빨래방": "laundromat", "셀프빨래방": "laundromat", "코인세탁": "laundromat", "코인빨래방": "laundromat",
    "약국": "pharmacy",
    "병원": "clinic", "의원": "clinic", "내과": "clinic",
    "술집": "bar", "주점": "bar", "호프": "bar", "호프집": "bar", "유흥": "bar",
    "노래방": "karaoke", "코인노래방": "karaoke",
    "헬스장": "gym", "헬스": "gym", "피트니스": "gym", "헬스클럽": "gym",
}
# 카카오 카테고리 그룹 코드 → 상가정보 분류 (엔진의 Condition.category 를 그대로 받을 때)
KAKAO_CATEGORIES: dict[str, str] = {"CS2": "convenience", "MT1": "supermarket", "PM9": "pharmacy", "HP8": "clinic"}


def local_category(text: str | None) -> str | None:
    """장소 이름·카테고리 코드·분류 키 → 상가정보 분류. 모르면 None."""
    if not text:
        return None
    t = text.replace(" ", "")
    if t in CATEGORY_INFO:
        return t
    return KEYWORDS.get(t) or KAKAO_CATEGORIES.get(t.upper())


def category_label(cat: str) -> str:
    return CATEGORY_INFO[cat][0]


class LocalStoreSearcher:
    """상가정보로 반경 검색. 카카오처럼 호출 수 제한이 필요 없다 (unlimited)."""

    unlimited = True

    def __init__(self, stores: tuple[Store, ...] | None = None):
        self.stores = load_stores() if stores is None else stores
        self._by_cat: dict[str, list[Store]] = {}
        for s in self.stores:
            self._by_cat.setdefault(s.category, []).append(s)

    def search(self, *, lat: float, lng: float, radius_m: int, query: str | None, category: str | None,
               page: int, size: int) -> dict[str, Any]:
        cat = local_category(query) or local_category(category)
        if cat is None:
            raise ValueError(f"상가정보로 확인할 수 없는 장소: {query or category}")
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * math.cos(math.radians(lat)))
        hits = []
        for s in self._by_cat.get(cat, []):
            if abs(s.lat - lat) <= dlat and abs(s.lon - lng) <= dlon:
                d = distance_m(lat, lng, s.lat, s.lon)
                if d <= radius_m:
                    hits.append((d, s))
        hits.sort(key=lambda x: x[0])
        start = (page - 1) * size
        docs = [{"id": f"{s.category}:{s.name}:{s.lat:.6f}:{s.lon:.6f}", "place_name": s.name, "distance": str(round(d))}
                for d, s in hits[start:start + size]]
        return {"documents": docs, "meta": {"is_end": start + size >= len(hits), "total_count": len(hits)}}


def place_searcher() -> Any | None:
    """제외 조건 장소 확인에 쓸 검색기. local(기본): 상가정보, kakao: 카카오 Local (키 필요, 호출 상한 있음)."""
    mode = (_read("PLACE_SEARCH") or "local").strip().lower()
    if mode == "kakao":
        return None  # 카카오는 호출하는 쪽(routers.recommend)이 키·클라이언트를 열고 닫는다
    s = LocalStoreSearcher()
    return s if s.stores else None
