"""챗봇 제외 조건: 확인 계획 형식과 확인 도구 실행 (추천 시스템 통합안 5장).

⚠️ 챗봇(5장)은 미완성이다. 여기에는 LLM 이 만들 '확인 계획'의 형식과, 사용자가 승인한 계획을
코드가 매물마다 실행하는 부분만 있다. 계획 JSON 을 POST /recommend 의 exclusions 에 직접 넣어 테스트할 수 있다.

- LLM 은 매물을 직접 판단하지 않는다. 어떤 도구로 무엇을 확인할지만 정한다 (5.1).
- 같은 계획이면 결과가 항상 같다. 같은 매물·도구·키워드·반경의 검색 결과는 캐시한다 (프로세스 메모리만,
  파일·DB 에 저장하지 않는다. 카카오 로컬 API 는 결과 저장이 금지라 실시간 호출로만 쓴다).
- 자주 쓰는 주변 시설 8종(편의점·마트·세탁소·코인빨래방·약국·병원·술집·노래방)은 공공데이터로 미리 계산한
  매물 칸(amenity 도구)을 먼저 쓰고, 카카오(nearby_count·nearest_distance)는 그 밖의 장소에만 쓴다.
- 도구: nearby_count·nearest_distance(카카오), listing_field(매물 칸), building_field(건축물대장), amenity(주변 시설).
  LLM 에 주는 칸 목록은 tool_catalog() 가 코드의 칸 정의에서 만든다.
- 매물 좌표가 없으면 장소 도구를 실행할 수 없다. 그 매물은 빼지 않고 notes 에 개수를 남긴다 (5.4).
- 추천 한 번에 카카오 호출 수에 상한을 둔다 (설정 kakao_call_limit). 넘으면 남은 매물은 확인하지 않고 안내한다.
"""

import math
import operator
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from app.models import Property
from app.scoring_config import CONFIG, ScoringConfig

Op = Literal[">", ">=", "<", "<=", "==", "!=", "in", "not_in"]
NUM_OPS = (">", ">=", "<", "<=", "==", "!=")
_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq, "!=": operator.ne,
}
_OP_WORDS = {">": "초과", ">=": "이상", "<": "미만", "<=": "이하", "==": "", "!=": "아님"}

Tool = Literal["nearby_count", "nearest_distance", "listing_field", "building_field", "amenity"]
MAX_RADIUS_M = 20000  # 카카오 장소 검색 반경 최대값


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침을 보고 조사를 고른다 (이/가, 을/를, 은/는). 마지막 한글 글자 기준, 한글이 없으면 뒤쪽 것."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            return with_final if (ord(ch) - 0xAC00) % 28 else without_final
        if ch.isdigit():
            return with_final if ch in "013678" else without_final
    return without_final


# ---------------------------------------------------------------- 확인할 수 있는 칸 (도구별)

@dataclass(frozen=True)
class FieldSpec:
    """도구가 읽는 칸 하나. get 은 매물에서 값을 꺼낸다 (모르면 None)."""

    label: str
    unit: str
    kind: Literal["num", "cat"]
    get: Callable[[Property, ScoringConfig], float | str | None]
    desc: str
    choices: tuple[str, ...] = ()


def _floor_number(p: Property) -> float | None:
    """해당 층 숫자. 반지하 0, 지하N −N, 옥탑방은 전체 층 + 1. 저층·중층·고층처럼 숫자가 없으면 None."""
    f = (p.floor or "").strip()
    if not f:
        return None
    if f == "반지하":
        return 0.0
    if f.startswith("지하"):
        n = f[2:].strip()
        return -float(n) if n.isdigit() else -1.0
    if f.startswith("옥탑"):
        total = _building_floors(p)
        return total + 1 if total is not None else None
    return float(f) if f.isdigit() else None


def _floor_kind(p: Property) -> str | None:
    f = (p.floor or "").strip()
    if not f:
        return None
    if f == "반지하":
        return "반지하"
    if f.startswith("지하"):
        return "지하"
    if f.startswith("옥탑"):
        return "옥탑"
    if f.isdigit():
        return "1층" if int(f) == 1 else "2층 이상"
    return "지상(층 모름)"  # 저층·중층·고층


def _building_floors(p: Property) -> float | None:
    b = (p.building_floor or "").strip()
    if b.isdigit():
        return float(b)
    return _bld(p, p.bld_floors_above)


def _bld(p: Property, value):
    """건축물대장 값은 주소로 건물 하나가 정확히 찾아졌을 때만 쓴다."""
    return value if p.bld_status == "matched" else None


def _stairs_floor(p: Property) -> float | None:
    """엘리베이터 없이 걸어 올라가는 층. 엘리베이터가 있으면 0, 층이나 엘리베이터를 모르면 None."""
    elev = _bld(p, p.bld_elevators)
    if elev is None:
        return None
    if elev > 0:
        return 0.0
    n = _floor_number(p)
    return None if n is None else max(n, 0.0)


def _monthly_cost(p: Property) -> float | None:
    if p.monthly_housing_cost_manwon is not None:
        return p.monthly_housing_cost_manwon
    if p.monthly_rent_manwon is None:
        return None
    return p.monthly_rent_manwon + (p.maintenance_manwon or 0)


def _main_use(p: Property) -> str | None:
    v = _bld(p, p.bld_main_purpose)
    if not v:
        return None
    if v in ("단독주택", "공동주택"):
        return "주거용(단독·공동주택)"
    if "근린생활시설" in v:
        return "근린생활시설"
    if v == "업무시설":
        return "업무시설(오피스텔)"
    return "기타"


def _structure(p: Property) -> str | None:
    v = _bld(p, p.bld_structure)
    if not v:
        return None
    if "철근콘크리트" in v or "철골콘크리트" in v:
        return "철근콘크리트"
    if any(k in v for k in ("벽돌", "블록", "조적")):
        return "조적(벽돌·블록)"
    if "목구조" in v:
        return "목조"
    if "철골" in v:
        return "철골"
    return "기타"


def _age(p: Property, cfg: ScoringConfig) -> float | None:
    year = _bld(p, p.bld_use_approval_year)
    if year is None:
        return None
    ref = cfg.reference_year or date.today().year
    return max(0.0, ref - year)


LISTING_FIELDS: dict[str, FieldSpec] = {
    "area_m2": FieldSpec("면적", "㎡", "num", lambda p, c: p.area_m2, "전용면적"),
    "deposit_manwon": FieldSpec("보증금", "만 원", "num", lambda p, c: p.deposit_manwon, "보증금"),
    "monthly_rent_manwon": FieldSpec("월세", "만 원", "num", lambda p, c: p.monthly_rent_manwon, "월세"),
    "maintenance_manwon": FieldSpec("관리비", "만 원", "num", lambda p, c: p.maintenance_manwon, "관리비"),
    "monthly_cost_manwon": FieldSpec("월 고정 주거비", "만 원", "num", lambda p, c: _monthly_cost(p), "월세 + 관리비"),
    "floor_kind": FieldSpec(
        "층 종류", "", "cat", lambda p, c: _floor_kind(p), "반지하·지하·옥탑·1층·2층 이상. 저층/중층/고층으로만 나온 매물은 '지상(층 모름)'",
        ("반지하", "지하", "옥탑", "1층", "2층 이상", "지상(층 모름)"),
    ),
    "floor_number": FieldSpec("층", "층", "num", lambda p, c: _floor_number(p), "해당 층 숫자. 반지하 0, 지하는 음수, 저/중/고층은 모름"),
    "building_floors": FieldSpec("건물 전체 층수", "층", "num", lambda p, c: _building_floors(p), "건물 지상 층수"),
    "stairs_floor": FieldSpec(
        "엘리베이터 없이 올라가는 층", "층", "num", lambda p, c: _stairs_floor(p),
        "엘리베이터가 없으면 해당 층, 있으면 0 (엘리베이터는 건축물대장 기준)",
    ),
    "room_type": FieldSpec(
        "방 구조", "", "cat", lambda p, c: p.room_type, "오픈형·분리형·복층형 원룸, 투룸",
        ("오픈형원룸", "분리형원룸", "복층형원룸", "투룸"),
    ),
    "property_type": FieldSpec("매물 유형", "", "cat", lambda p, c: p.property_type, "원룸·오피스텔", ("원룸", "오피스텔")),
}

BUILDING_FIELDS: dict[str, FieldSpec] = {
    "building_age_years": FieldSpec("건물 연식", "년", "num", _age, "사용승인 후 지난 햇수 (신축일수록 작다)"),
    "approval_year": FieldSpec("사용승인 연도", "년", "num", lambda p, c: _bld(p, p.bld_use_approval_year), "건물 사용승인 연도"),
    "main_use": FieldSpec(
        "건물 용도", "", "cat", lambda p, c: _main_use(p),
        "근린생활시설은 이른바 근생 원룸 (주거용이 아니라 전입신고·대출이 막힐 수 있음)",
        ("주거용(단독·공동주택)", "근린생활시설", "업무시설(오피스텔)", "기타"),
    ),
    "structure": FieldSpec(
        "건물 구조", "", "cat", lambda p, c: _structure(p), "방음 간접 지표 (조적·목조는 철근콘크리트보다 소리가 잘 샌다)",
        ("철근콘크리트", "조적(벽돌·블록)", "목조", "철골", "기타"),
    ),
    "floors_above": FieldSpec("건물 지상 층수", "층", "num", lambda p, c: _bld(p, p.bld_floors_above), "건축물대장 지상 층수"),
    "elevators": FieldSpec("승강기 수", "대", "num", lambda p, c: _bld(p, p.bld_elevators), "승용 승강기 수"),
    "parking_spaces": FieldSpec("주차 대수", "대", "num", lambda p, c: _bld(p, p.bld_parking), "건물 전체 주차 대수"),
    "households": FieldSpec("세대 수", "세대", "num", lambda p, c: _bld(p, p.bld_households), "건물 세대(가구) 수"),
}

AMENITIES: dict[str, str] = {
    "convenience": "편의점", "supermarket": "마트·슈퍼", "laundry": "세탁소", "laundromat": "코인빨래방",
    "pharmacy": "약국", "clinic": "병원·의원", "bar": "술집", "karaoke": "노래방",
}
AmenityMetric = Literal["nearest_m", "count_500m"]

# 예전 이름 (호환)
FIELD_LABELS: dict[str, tuple[str, str]] = {k: (v.label, v.unit) for k, v in LISTING_FIELDS.items()}


def _fields_for(tool: str) -> dict[str, FieldSpec]:
    return LISTING_FIELDS if tool == "listing_field" else BUILDING_FIELDS


# ---------------------------------------------------------------- 5.3장 확인 계획 형식

class ExcludeIf(BaseModel):
    """숫자 칸은 > >= < <= == != 와 숫자, 종류 칸은 in / not_in 과 값 목록."""

    op: Op
    value: float | list[Annotated[str, Field(min_length=1)]]

    @model_validator(mode="after")
    def _value_matches_op(self) -> "ExcludeIf":
        if self.op in ("in", "not_in"):
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.op} 에는 값 목록이 필요합니다.")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.op} 에는 숫자 값이 필요합니다.")
        return self


class Condition(BaseModel):
    """확인 조건 하나. tool 마다 필요한 칸이 다르다.

    - nearby_count:     (카카오, 실시간) 매물 반경 radius_m 안 keywords(또는 category) 장소 개수
    - nearest_distance: (카카오, 실시간) 가장 가까운 keywords(또는 category) 장소까지 거리(m). radius_m 안에 없으면 '없음'
    - listing_field:    매물 칸(field) 값 — LISTING_FIELDS
    - building_field:   건축물대장 칸(field) 값 — BUILDING_FIELDS
    - amenity:          미리 계산한 주변 시설(amenity) 의 가장 가까운 거리(nearest_m) 또는 500m 안 개수(count_500m)
    """

    tool: Tool
    label: Annotated[str, Field(min_length=1, description="화면·사유에 쓰는 이름 (예: 술집)")]
    keywords: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    category: str | None = Field(default=None, description="카카오 카테고리 그룹 코드 (예: CS2 편의점)")
    radius_m: Annotated[int, Field(gt=0, le=MAX_RADIUS_M)] | None = None
    field: str | None = None
    amenity: str | None = None
    metric: AmenityMetric | None = None
    exclude_if: ExcludeIf
    note: str = Field(default="", description="간접 확인일 때 그 사실 (예: 방음을 건물 구조로 간접 확인)")

    @model_validator(mode="after")
    def _fields_for_tool(self) -> "Condition":
        op = self.exclude_if.op
        if self.tool in ("nearby_count", "nearest_distance"):
            if not (self.keywords or self.category):
                raise ValueError(f"{self.tool} 에는 keywords 또는 category 가 필요합니다.")
            if self.tool == "nearby_count" and self.radius_m is None:
                raise ValueError("nearby_count 에는 radius_m 이 필요합니다.")
            if op not in NUM_OPS:
                raise ValueError(f"{self.tool} 에는 숫자 비교만 쓸 수 있습니다.")
        elif self.tool in ("listing_field", "building_field"):
            fields = _fields_for(self.tool)
            if self.field not in fields:
                raise ValueError(f"{self.tool} 의 field 는 {', '.join(fields)} 중 하나여야 합니다.")
            spec = fields[self.field]
            if spec.kind == "num" and op not in NUM_OPS:
                raise ValueError(f"'{self.field}' 는 숫자 칸이라 숫자 비교만 쓸 수 있습니다.")
            if spec.kind == "cat":
                if op not in ("in", "not_in"):
                    raise ValueError(f"'{self.field}' 는 종류 칸이라 in / not_in 만 쓸 수 있습니다.")
                bad = [v for v in self.exclude_if.value if v not in spec.choices]  # type: ignore[union-attr]
                if bad:
                    raise ValueError(f"'{self.field}' 값은 {', '.join(spec.choices)} 중에서 고릅니다 (받은 값: {', '.join(bad)}).")
        elif self.tool == "amenity":
            if self.amenity not in AMENITIES:
                raise ValueError(f"amenity 는 {', '.join(AMENITIES)} 중 하나여야 합니다.")
            if self.metric is None:
                raise ValueError("amenity 에는 metric(nearest_m 또는 count_500m)이 필요합니다.")
            if op not in NUM_OPS:
                raise ValueError("amenity 에는 숫자 비교만 쓸 수 있습니다.")
        return self

    @property
    def uses_places(self) -> bool:
        return self.tool in ("nearby_count", "nearest_distance")

    def describe(self) -> str:
        """사용자에게 보여 줄 확인 문장 한 줄 (서버가 만든다 — LLM 문장과 실행 내용이 어긋나지 않게)."""
        e = self.exclude_if
        if e.op in ("in", "not_in"):
            vals = "·".join(e.value)  # type: ignore[arg-type]
            assert self.field is not None
            name = _fields_for(self.tool)[self.field].label
            subj = f"{name}{josa(name, '이', '가')}"
            text = f"{subj} {vals}인 매물 제외" if e.op == "in" else f"{subj} {vals}{josa(vals, '이', '가')} 아닌 매물 제외"
        else:
            name, unit = self._name_unit()
            word = _OP_WORDS[e.op]
            text = f"{name} {_fmt(e.value)}{unit}{(' ' + word) if word else ''}인 매물 제외"  # type: ignore[arg-type]
        source = {
            "nearby_count": "카카오 장소 검색", "nearest_distance": "카카오 장소 검색", "listing_field": "매물 정보",
            "building_field": "건축물대장", "amenity": "공공데이터 주변 시설",
        }[self.tool]
        return f"{text} ({source}{', ' + self.note if self.note else ''})"

    def _name_unit(self) -> tuple[str, str]:
        if self.tool in ("listing_field", "building_field"):
            spec = _fields_for(self.tool)[self.field]  # type: ignore[index]
            return spec.label, spec.unit
        if self.tool == "amenity":
            a = AMENITIES[self.amenity]  # type: ignore[index]
            return (f"가장 가까운 {a}까지 거리", "m") if self.metric == "nearest_m" else (f"500m 안 {a} 수", "곳")
        if self.tool == "nearby_count":
            return f"반경 {self.radius_m}m 안 {self.label} 수", "곳"
        return f"가장 가까운 {self.label}까지 거리", "m"


class ExclusionPlan(BaseModel):
    """사용자가 승인한 확인 계획 (LLM 이 사용자 말에서 만든다 — 챗봇 미완성)."""

    source_text: str = ""
    conditions: list[Condition] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="도구로 확인할 수 없는 기호 (추측해서 빼지 않는다)")
    onsite_checks: list[str] = Field(default_factory=list, description="데이터로는 못 보고 집 보러 가서 확인할 것")
    confirm_message: str = ""

    def confirm_text(self) -> str:
        """확인 질문 문장. 조건 문장은 Condition.describe() (서버가 만든 것)만 쓴다."""
        src = self.source_text
        head = f"'{src}'{josa(src, '을', '를')} 이렇게 확인할게요." if src else "이렇게 확인할게요."
        lines = [head, *[f"{i}. {c.describe()}" for i, c in enumerate(self.conditions, 1)]]
        for u in self.unsupported:
            lines.append(f"- '{u}'{josa(u, '은', '는')} 지금 데이터로 확인할 수 없어 매물을 빼지 않아요.")
        if self.onsite_checks:
            lines.append("- 집 보러 가면 확인해 보세요: " + ", ".join(self.onsite_checks))
        lines.append("이렇게 할까요? (네 / 아니요)" if self.conditions else "빼는 매물은 없어요.")
        return "\n".join(lines)


def tool_catalog() -> dict[str, Any]:
    """확인 계획을 만드는 LLM 에 줄 도구 목록 (프롬프트·명세용). 코드의 칸 정의에서 만든다."""

    def fields(d: dict[str, FieldSpec]) -> list[dict[str, Any]]:
        return [{"field": k, "label": v.label, "kind": v.kind, "unit": v.unit, "desc": v.desc,
                 **({"choices": list(v.choices)} if v.choices else {})} for k, v in d.items()]

    return {
        "ops": {"num": list(NUM_OPS), "cat": ["in", "not_in"]},
        "tools": {
            "listing_field": {"desc": "매물 정보 칸", "fields": fields(LISTING_FIELDS)},
            "building_field": {"desc": "건축물대장 (주소로 건물이 정확히 찾아진 매물만)", "fields": fields(BUILDING_FIELDS)},
            "amenity": {
                "desc": "미리 계산한 주변 시설. nearest_m = 가장 가까운 곳까지 m, count_500m = 500m 안 개수. "
                        "500m 안에 없으면 거리는 '500m 넘음'으로 본다",
                "amenities": AMENITIES,
                "metrics": ["nearest_m", "count_500m"],
            },
            "nearby_count": {"desc": "카카오 장소 검색(실시간): 반경 radius_m 안 keywords 장소 수. amenity 에 없는 장소에만 쓴다"},
            "nearest_distance": {"desc": "카카오 장소 검색(실시간): 가장 가까운 keywords 장소까지 m. amenity 에 없는 장소에만 쓴다"},
        },
    }


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
        return bool(self.conditions) or any(p.unsupported or p.onsite_checks for p in self.plans)

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
        test = _OPS.get(c.exclude_if.op)  # in / not_in 은 종류 칸에서 따로 처리
        threshold = c.exclude_if.value
        if c.tool in ("listing_field", "building_field"):
            assert c.field is not None
            spec = _fields_for(c.tool)[c.field]
            value = spec.get(p, self.cfg)
            if value is None:
                self.field_missing.setdefault(c.label, set()).add(p.property_id)
                return None
            if spec.kind == "cat":
                listed = value in c.exclude_if.value  # type: ignore[operator]
                if listed == (c.exclude_if.op == "in"):
                    return f"{spec.label}: {value}"
                return None
            if test(value, threshold):  # type: ignore[arg-type]
                return f"{spec.label} {_fmt(value)}{spec.unit} (조건: {spec.label} {c.exclude_if.op} {_fmt(threshold)}{spec.unit})"  # type: ignore[arg-type]
            return None

        if c.tool == "amenity":
            return self._check_amenity(p, c, test, threshold)  # type: ignore[arg-type]

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

    def _check_amenity(self, p: Property, c: Condition, test, threshold: float) -> str | None:
        assert c.amenity is not None
        name = AMENITIES[c.amenity]
        radius = self.cfg.amenity_known_radius_m
        count = getattr(p, f"count_500m_{c.amenity}")
        if c.metric == "count_500m":
            if count is None:
                self.field_missing.setdefault(c.label, set()).add(p.property_id)
                return None
            return f"500m 안 {name} {_fmt(count)}곳" if test(count, threshold) else None
        dist = getattr(p, f"nearest_m_{c.amenity}")
        if dist is None:
            # 거리가 비고 500m 안 개수가 0 이면 '500m 안에 없음'. 500m 를 넘는 어느 거리여도 결과가 같을 때만 판단한다
            if count == 0 and test(math.inf, threshold) == test(radius + 1e-6, threshold):
                return f"{radius}m 안에 {name} 없음" if test(math.inf, threshold) else None
            self.field_missing.setdefault(c.label, set()).add(p.property_id)
            return None
        return f"가장 가까운 {name}까지 {_fmt(dist)}m" if test(dist, threshold) else None

    # ---- 결과 안내

    def notes(self) -> list[str]:
        out: list[str] = []
        for plan in self.plans:
            for item in plan.unsupported:
                out.append(f"'{item}'{josa(item, '은', '는')} 아직 확인할 수 없어요. 이 조건으로는 매물을 빼지 않았습니다.")
            if plan.onsite_checks:
                out.append("집 보러 가면 확인해 보세요: " + ", ".join(plan.onsite_checks))
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
