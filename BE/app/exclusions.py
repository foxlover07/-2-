"""챗봇 제외 조건: 확인 계획 형식과 확인 도구 실행 (추천 시스템 통합안 5장).

⚠️ 챗봇(5장)은 미완성이다. 여기에는 LLM 이 만들 '확인 계획'의 형식과, 사용자가 승인한 계획을
코드가 매물마다 실행하는 부분만 있다. 계획 JSON 을 POST /recommend 의 exclusions 에 직접 넣어 테스트할 수 있다.

- LLM 은 매물을 직접 판단하지 않는다. 어떤 도구로 무엇을 확인할지만 정한다 (5.1).
- 같은 계획이면 결과가 항상 같다. 같은 매물·도구·키워드·반경의 검색 결과는 캐시한다 (프로세스 메모리만,
  파일·DB 에 저장하지 않는다. 카카오 결과 저장 정책은 확인 필요).
- 매물 좌표가 없으면 장소 도구를 실행할 수 없다. 그 매물은 빼지 않고 notes 에 개수를 남긴다 (5.4).
- 추천 한 번에 카카오 호출 수에 상한을 둔다 (설정 kakao_call_limit). 넘으면 남은 매물은 확인하지 않고 안내한다.
"""

import math
import operator
from collections.abc import Callable, Iterable
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from app.models import Property
from app.scoring_config import CONFIG, ScoringConfig

Op = Literal[">", ">=", "<", "<=", "==", "!="]
_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne,
}

Tool = Literal["nearby_count", "nearest_distance", "listing_field"]
ListingField = Literal["area_m2", "deposit_manwon", "monthly_rent_manwon", "maintenance_manwon"]
FIELD_LABELS: dict[str, tuple[str, str]] = {
    "area_m2": ("면적", "㎡"),
    "deposit_manwon": ("보증금", "만 원"),
    "monthly_rent_manwon": ("월세", "만 원"),
    "maintenance_manwon": ("관리비", "만 원"),
}
MAX_RADIUS_M = 20000  # 카카오 장소 검색 반경 최대값


# ---------------------------------------------------------------- 5.3장 확인 계획 형식

class ExcludeIf(BaseModel):
    op: Op
    value: float


class Condition(BaseModel):
    """확인 조건 하나. tool 마다 필요한 칸이 다르다.

    - nearby_count:     매물 반경 radius_m 안 keywords(또는 category) 장소 개수
    - nearest_distance: 가장 가까운 keywords(또는 category) 장소까지 거리(m). radius_m 안에 없으면 '없음'(무한대로 비교)
    - listing_field:    매물 칸(field) 값
    """

    tool: Tool
    label: Annotated[str, Field(min_length=1, description="화면·사유에 쓰는 이름 (예: 술집)")]
    keywords: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    category: str | None = Field(default=None, description="카카오 카테고리 그룹 코드 (예: CS2 편의점)")
    radius_m: Annotated[int, Field(gt=0, le=MAX_RADIUS_M)] | None = None
    field: ListingField | None = None
    exclude_if: ExcludeIf

    @model_validator(mode="after")
    def _fields_for_tool(self) -> "Condition":
        if self.tool in ("nearby_count", "nearest_distance") and not (self.keywords or self.category):
            raise ValueError(f"{self.tool} 에는 keywords 또는 category 가 필요합니다.")
        if self.tool == "nearby_count" and self.radius_m is None:
            raise ValueError("nearby_count 에는 radius_m 이 필요합니다.")
        if self.tool == "listing_field" and self.field is None:
            raise ValueError("listing_field 에는 field 가 필요합니다.")
        return self

    @property
    def uses_places(self) -> bool:
        return self.tool in ("nearby_count", "nearest_distance")


class ExclusionPlan(BaseModel):
    """사용자가 승인한 확인 계획 (LLM 이 사용자 말에서 만든다 — 챗봇 미완성)."""

    source_text: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="도구로 확인할 수 없는 기호 (추측해서 빼지 않는다)")
    confirm_message: str = ""


# ---------------------------------------------------------------- 장소 검색 (카카오)

class PlaceSearcher(Protocol):
    """카카오 Local 장소 검색과 같은 모양의 응답을 돌려준다 ({documents: [{id, distance}], meta: {is_end}})."""

    def search(
        self, *, lat: float, lng: float, radius_m: int, query: str | None, category: str | None, page: int, size: int
    ) -> dict: ...


class KakaoPlaceSearcher:
    def __init__(self, client: Any):
        self._client = client

    def search(self, *, lat, lng, radius_m, query, category, page, size) -> dict:
        from app.tools.kakao import LatLng

        return self._client.search_places_near(
            center=LatLng(lat=lat, lng=lng), radius_m=radius_m, query=query,
            category_group_code=category, page=page, size=size,
        )


# 같은 매물·도구·키워드·반경 → 결과 (프로세스 메모리 캐시)
_CACHE: dict[tuple, float | None] = {}


class _CallLimit(Exception):
    pass


def _fmt(v: float) -> str:
    return f"{v:g}"


class ExclusionRunner:
    """승인된 계획을 매물마다 실행한다. check(p) → (조건 이름, 제외 사유) 또는 None."""

    def __init__(
        self,
        plans: Iterable[ExclusionPlan],
        searcher: PlaceSearcher | None,
        cfg: ScoringConfig = CONFIG,
        cache: dict[tuple, float | None] | None = None,
    ):
        self.plans = list(plans)
        self.conditions = [c for plan in self.plans for c in plan.conditions]
        self.searcher = searcher
        self.cfg = cfg
        self.cache = _CACHE if cache is None else cache
        self.calls = 0
        self.no_coords: set[str] = set()
        self.over_limit: set[str] = set()
        self.search_errors: set[str] = set()
        self.field_missing: dict[str, set[str]] = {}
        self.place_labels_skipped_no_key: list[str] = []
        if searcher is None:
            self.place_labels_skipped_no_key = [c.label for c in self.conditions if c.uses_places]

    @property
    def active(self) -> bool:
        return bool(self.conditions) or any(p.unsupported for p in self.plans)

    # ---- 도구

    def _search(self, **kw) -> dict:
        if self.calls >= self.cfg.kakao_call_limit:
            raise _CallLimit
        self.calls += 1
        assert self.searcher is not None
        return self.searcher.search(**kw)

    def _queries(self, c: Condition) -> list[str | None]:
        return list(c.keywords) or [None]

    def _nearby_count(self, p: Property, c: Condition) -> tuple[float, bool]:
        """(개수, 상한에 걸려 더 있을 수 있는지)."""
        key = (p.property_id, c.tool, tuple(c.keywords), c.category, c.radius_m)
        capped_key = key + ("capped",)
        if key in self.cache:
            return self.cache[key] or 0.0, bool(self.cache.get(capped_key))
        ids: set[str] = set()
        capped = False
        for q in self._queries(c):
            for page in range(1, self.cfg.kakao_max_pages + 1):
                res = self._search(lat=p.latitude, lng=p.longitude, radius_m=c.radius_m, query=q,
                                   category=c.category, page=page, size=self.cfg.kakao_page_size)
                ids.update(str(d.get("id")) for d in res.get("documents", []))
                if res.get("meta", {}).get("is_end", True):
                    break
            else:
                capped = True
        self.cache[key] = float(len(ids))
        self.cache[capped_key] = 1.0 if capped else 0.0
        return float(len(ids)), capped

    def _nearest_distance(self, p: Property, c: Condition) -> float | None:
        radius = c.radius_m or self.cfg.nearest_search_radius_m
        key = (p.property_id, c.tool, tuple(c.keywords), c.category, radius)
        if key in self.cache:
            return self.cache[key]
        best: float | None = None
        for q in self._queries(c):
            res = self._search(lat=p.latitude, lng=p.longitude, radius_m=radius, query=q, category=c.category, page=1, size=1)
            for d in res.get("documents", []):
                try:
                    dist = float(d.get("distance"))
                except (TypeError, ValueError):
                    continue
                if dist <= radius and (best is None or dist < best):
                    best = dist
        self.cache[key] = best
        return best

    # ---- 매물 하나 확인

    def check(self, p: Property) -> tuple[str, str] | None:
        for c in self.conditions:
            hit = self._check_one(p, c)
            if hit:
                return c.label, hit
        return None

    def _check_one(self, p: Property, c: Condition) -> str | None:
        test = _OPS[c.exclude_if.op]
        threshold = c.exclude_if.value
        if c.tool == "listing_field":
            assert c.field is not None
            value = getattr(p, c.field)
            name, unit = FIELD_LABELS[c.field]
            if value is None:
                self.field_missing.setdefault(c.label, set()).add(p.property_id)
                return None
            if test(value, threshold):
                return f"{name} {_fmt(value)}{unit} (조건: {name} {c.exclude_if.op} {_fmt(threshold)}{unit})"
            return None

        # 장소 도구
        if self.searcher is None:
            return None
        if p.latitude is None or p.longitude is None:
            self.no_coords.add(p.property_id)
            return None
        try:
            if c.tool == "nearby_count":
                count, capped = self._nearby_count(p, c)
                if test(count, threshold):
                    more = " 이상" if capped else ""
                    return f"주변 {c.radius_m}m 안 {c.label} {_fmt(count)}곳{more}"
                return None
            dist = self._nearest_distance(p, c)
            radius = c.radius_m or self.cfg.nearest_search_radius_m
            if test(math.inf if dist is None else dist, threshold):
                return f"반경 {radius}m 안 {c.label} 없음" if dist is None else f"가장 가까운 {c.label}까지 {_fmt(dist)}m"
            return None
        except _CallLimit:
            self.over_limit.add(p.property_id)
            return None
        except Exception:  # 네트워크·카카오 오류: 추측해서 빼지 않는다
            self.search_errors.add(p.property_id)
            return None

    # ---- 결과 안내

    def notes(self) -> list[str]:
        out: list[str] = []
        for plan in self.plans:
            for item in plan.unsupported:
                out.append(f"'{item}'은(는) 아직 확인할 수 없어요. 이 조건으로는 매물을 빼지 않았습니다.")
        if self.place_labels_skipped_no_key:
            out.append(
                f"카카오 키가 없어 장소 조건({', '.join(dict.fromkeys(self.place_labels_skipped_no_key))})을 확인하지 못했습니다."
            )
        if self.no_coords:
            out.append(f"좌표가 없어 장소 조건을 확인하지 못한 매물 {len(self.no_coords)}개 (빼지 않았습니다).")
        if self.over_limit:
            out.append(
                f"카카오 호출 상한({self.cfg.kakao_call_limit}회)에 걸려 매물 {len(self.over_limit)}개는 장소 조건을 "
                "확인하지 못했습니다. 조건을 줄여 주세요."
            )
        if self.search_errors:
            out.append(f"장소 검색 오류로 확인하지 못한 매물 {len(self.search_errors)}개 (빼지 않았습니다).")
        for label, ids in self.field_missing.items():
            out.append(f"'{label}' 조건: 정보가 없어 확인하지 못한 매물 {len(ids)}개 (빼지 않았습니다).")
        return out

    def assumptions(self) -> dict[str, Any]:
        return {
            "plans": [p.model_dump() for p in self.plans],
            "kakao_calls": self.calls,
            "kakao_call_limit": self.cfg.kakao_call_limit,
            "nearest_search_radius_m": self.cfg.nearest_search_radius_m,
            "unchecked": {
                "no_coordinates": len(self.no_coords),
                "over_call_limit": len(self.over_limit),
                "search_errors": len(self.search_errors),
                "no_kakao_key": bool(self.place_labels_skipped_no_key),
            },
        }
