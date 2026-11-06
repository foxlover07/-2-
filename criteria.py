"""학생 맞춤 추천 근거 (MVP 4개 + 콘텐츠 기반) 와 다기준 점수 계산.

app/recommend.py 는 주거비·강의동 도보시간 두 기준만 다룬다. 이 모듈은 노션의
"학생 맞춤 추천 근거" 표에서 MVP 로 정한 근거를 기준으로 추가하고,
기준 개수에 상관없이 점수를 매기는 일반화된 가중합(MCDM)을 제공한다.

MVP 근거 (모두 LLM 없이 계산):
  1. weekly_commute_min    주간 총 통학 시간 (분, 낮을수록 좋음)
       요일마다 집 → 첫 수업 건물 + 마지막 수업 건물 → 집.
       수업 사이 건물 이동은 어느 집에 살든 같으므로 넣지 않는다.
  2. gap_home_count        공강 귀가 가능 횟수 (회, 높을수록 좋음)
       공강 ≥ (앞 수업 건물 → 집) + (집 → 다음 수업 건물) + 여유 시간 이면 1회.
  3. frequent_place_min    자주 가는 곳까지의 주간 이동시간 (분, 낮을수록 좋음, 알바 포함)
       from_campus=True (예: 수업 끝나고 바로 알바) 이면 학교 → 장소 구간은 집과 무관하므로
       장소 → 집 편도만 센다. 아니면 집 ↔ 장소 왕복.
  4. late_return_min       심야 귀가 시간 (분, 낮을수록 좋음)
       ends_at 이 LATE_NIGHT_FROM 이후(자정 넘김 포함)인 장소에서 집까지 × 주간 횟수.
       귀갓길 안전(CCTV·안심귀갓길)은 공공데이터 정제 후 콘텐츠 기반 특성 safe_route 로 반영한다.

콘텐츠 기반 추천:
  5. preference_match      선호 적합도 (0~1, 높을수록 좋음)
       매물 특성 벡터와 사용자 선호 벡터의 코사인 유사도. 정성 선호(풀옵션·조용함·
       귀갓길 안전 등)를 MCDM 의 기준 하나로 넣는다.

이동시간은 TravelTimes 에서 꺼낸다. 지금은 CSV 의 spotN_walk_min 과 사용자가 입력한
값으로 채우고, Route Tool(경로 API) 이 붙으면 같은 모양으로 채워 넣으면 된다.
이동시간이 없어 계산할 수 없는 매물은 app/recommend.py 와 같이 제외하고 이유를 남긴다.
"""

import math
import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.models import Property
from app.recommend import lower_is_better, monthly_fixed_cost

Day = Literal["월", "화", "수", "목", "금", "토", "일"]
Direction = Literal["lower", "higher"]

LATE_NIGHT_FROM = "22:00"   # 이 시각 이후에 끝나는 일정은 심야 귀가로 본다
LATE_NIGHT_UNTIL = "05:00"  # 자정을 넘겨 이 시각 전까지 끝나는 일정도 심야로 본다
DEFAULT_GAP_BUFFER_MIN = 30  # 공강에 집에 다녀오려면 이동시간 외에 최소 이만큼 머물 수 있어야 한다

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
Percent = Annotated[float, Field(ge=0, le=100)]

# 사용자가 화면에서 고르는 "자주 가는 지역" 목록 (이화여대 MVP 초안, 팀에서 수정).
# 매물마다 각 지역까지의 이동시간은 Route Tool(경로 API) 로 미리 계산해 TravelTimes 에 넣는다.
EWHA_AREAS: tuple[str, ...] = (
    "이대역",
    "신촌역",
    "홍대입구역",
    "아현역",
    "공덕역",
    "서울역",
    "광화문·시청",
    "여의도",
)


def to_minutes(hhmm: str) -> int:
    """'09:30' → 570."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _check_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError(f"시각은 HH:MM 형식이어야 합니다: {value!r}")
    return value


# ---------------------------------------------------------------- 입력 모델

class ClassSlot(BaseModel):
    """시간표 한 칸. Vision LLM 이 시간표 캡처에서 이 모양으로 뽑는다."""

    day: Day
    start: str
    end: str
    building: str  # CSV spotN_name 과 같은 이름 (예: "공과대학", "ECC")

    @field_validator("start", "end")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        return _check_time(v)


class FrequentPlace(BaseModel):
    """자주 가는 지역. 화면에서 EWHA_AREAS 중 하나를 고르고, 용도와 횟수를 입력한다."""

    area: str
    kind: Literal["work", "gym", "study", "home_town", "etc"] = "etc"
    visits_per_week: Annotated[int, Field(ge=0)]
    from_campus: bool = False  # 수업 끝나고 학교에서 바로 가는 곳이면 True
    ends_at: str | None = None  # 끝나는 시각. 심야 귀가 판단에 쓴다

    @field_validator("area")
    @classmethod
    def _validate_area(cls, v: str) -> str:
        if v not in EWHA_AREAS:
            raise ValueError(f"선택할 수 없는 지역입니다: {v!r} (가능: {', '.join(EWHA_AREAS)})")
        return v

    @field_validator("ends_at")
    @classmethod
    def _validate_ends_at(cls, v: str | None) -> str | None:
        return None if v is None else _check_time(v)


class Lifestyle(BaseModel):
    """학생의 생활 패턴과 기준별 반영 비율.

    weights_percent: 기준 키 → 반영 비율(%). 화면의 슬라이더 값을 그대로 받으며 합은 100.
      비워 두면 입력이 있는 기준에 똑같이 나눈다. 0% 인 기준은 계산하지 않는다.
    """

    timetable: list[ClassSlot] = Field(default_factory=list)
    places: list[FrequentPlace] = Field(default_factory=list)
    budget_manwon: Annotated[float, Field(ge=0)] | None = None
    preferences: dict[str, Annotated[float, Field(ge=0, le=1)]] = Field(default_factory=dict)
    gap_buffer_min: Annotated[int, Field(ge=0)] = DEFAULT_GAP_BUFFER_MIN
    weights_percent: dict[str, Percent] = Field(default_factory=dict)

    @field_validator("weights_percent")
    @classmethod
    def _validate_weights(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(v) - set(CRITERIA))
        if unknown:
            raise ValueError(f"알 수 없는 기준: {', '.join(unknown)}")
        if v and abs(sum(v.values()) - 100) > 0.5:
            raise ValueError(f"반영 비율의 합은 100% 여야 합니다 (지금 {sum(v.values()):g}%).")
        return v


# 매물 ID → 목적지 이름 → 이동시간(분). 목적지는 강의동 이름 또는 EWHA_AREAS 의 지역 이름.
TravelTimes = dict[str, dict[str, float]]

# 매물 ID → 특성 이름 → 0~1 (예: {"full_option": 1, "quiet": 0.7, "safe_route": 0.8})
PropertyFeatures = dict[str, dict[str, float]]


def travel_times_from_csv(properties: Iterable[Property]) -> TravelTimes:
    """CSV 의 spot1~4 도보시간으로 강의동 이동시간 표를 만든다."""
    table: TravelTimes = {}
    for p in properties:
        row: dict[str, float] = {}
        for i in range(1, 5):
            name = getattr(p, f"spot{i}_name")
            walk = getattr(p, f"spot{i}_walk_min")
            if name is not None and walk is not None:
                row[name] = walk
        table[p.property_id] = row
    return table


def merge_travel_times(base: TravelTimes, extra: Mapping[str, Mapping[str, float]]) -> TravelTimes:
    """기본 표에 사용자 입력·Route Tool 결과(자주 가는 곳까지의 시간)를 덧붙인다."""
    merged = {pid: dict(row) for pid, row in base.items()}
    for pid, row in extra.items():
        merged.setdefault(pid, {}).update(row)
    return merged


# ---------------------------------------------------------------- 근거 1~4 (값이 없으면 None)

def _by_day(timetable: Iterable[ClassSlot]) -> dict[str, list[ClassSlot]]:
    days: dict[str, list[ClassSlot]] = {}
    for slot in timetable:
        days.setdefault(slot.day, []).append(slot)
    for slots in days.values():
        slots.sort(key=lambda s: to_minutes(s.start))
    return days


def weekly_commute_min(times: Mapping[str, float], timetable: list[ClassSlot]) -> float | None:
    """1. 주간 총 통학 시간. 수업이 없으면 None (기준을 끈다)."""
    days = _by_day(timetable)
    if not days:
        return None
    total = 0.0
    for slots in days.values():
        first, last = slots[0].building, slots[-1].building
        if first not in times or last not in times:
            return None
        total += times[first] + times[last]
    return round(total, 1)


def gap_home_count(
    times: Mapping[str, float], timetable: list[ClassSlot], buffer_min: int = DEFAULT_GAP_BUFFER_MIN
) -> float | None:
    """2. 공강에 집에 다녀올 수 있는 횟수. 공강이 하나도 없으면 None (기준을 끈다)."""
    count = 0
    gaps = 0
    for slots in _by_day(timetable).values():
        for before, after in zip(slots, slots[1:]):
            gap = to_minutes(after.start) - to_minutes(before.end)
            if gap <= 0:
                continue
            gaps += 1
            if before.building not in times or after.building not in times:
                return None
            if gap >= times[before.building] + times[after.building] + buffer_min:
                count += 1
    return float(count) if gaps else None


def frequent_place_min(times: Mapping[str, float], places: list[FrequentPlace]) -> float | None:
    """3. 자주 가는 곳(알바 포함)까지의 주간 이동시간. 등록한 곳이 없으면 None."""
    active = [pl for pl in places if pl.visits_per_week > 0]
    if not active:
        return None
    total = 0.0
    for pl in active:
        if pl.area not in times:
            return None
        one_way = times[pl.area]
        total += pl.visits_per_week * (one_way if pl.from_campus else 2 * one_way)
    return round(total, 1)


def is_late_night(hhmm: str) -> bool:
    m = to_minutes(hhmm)
    return m >= to_minutes(LATE_NIGHT_FROM) or m < to_minutes(LATE_NIGHT_UNTIL)


def late_return_min(times: Mapping[str, float], places: list[FrequentPlace]) -> float | None:
    """4. 심야 귀가 시간 (장소 → 집 × 주간 횟수). 심야 일정이 없으면 None."""
    late = [pl for pl in places if pl.visits_per_week > 0 and pl.ends_at and is_late_night(pl.ends_at)]
    if not late:
        return None
    total = 0.0
    for pl in late:
        if pl.area not in times:
            return None
        total += pl.visits_per_week * times[pl.area]
    return round(total, 1)


# ---------------------------------------------------------------- 5. 콘텐츠 기반

def cosine_similarity(a: Mapping[str, float], b: Mapping[str, float]) -> float | None:
    """두 특성 벡터의 코사인 유사도 (0~1). 한쪽이 영벡터면 None."""
    keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return None
    return round(dot / (na * nb), 4)


def preference_match(features: Mapping[str, float], preferences: Mapping[str, float]) -> float | None:
    """사용자가 중요하다고 한 특성만으로 매물과의 유사도를 잰다. 선호가 없으면 None."""
    if not any(v > 0 for v in preferences.values()):
        return None
    relevant = {k: features.get(k, 0.0) for k in preferences}
    return cosine_similarity(relevant, preferences) or 0.0


# ---------------------------------------------------------------- 기준 정의

class CriterionSpec(BaseModel):
    key: str
    label: str
    direction: Direction
    unit: str
    algorithm: Literal["MCDM", "콘텐츠 기반"]


CRITERIA: dict[str, CriterionSpec] = {
    c.key: c
    for c in [
        CriterionSpec(key="monthly_cost", label="월 고정 주거비", direction="lower", unit="만원", algorithm="MCDM"),
        CriterionSpec(key="weekly_commute", label="주간 총 통학 시간", direction="lower", unit="분", algorithm="MCDM"),
        CriterionSpec(key="gap_home", label="공강 귀가 가능 횟수", direction="higher", unit="회", algorithm="MCDM"),
        CriterionSpec(key="frequent_place", label="자주 가는 곳 이동시간(알바 포함)", direction="lower", unit="분", algorithm="MCDM"),
        CriterionSpec(key="late_return", label="심야 귀가 시간", direction="lower", unit="분", algorithm="MCDM"),
        CriterionSpec(key="preference_match", label="선호 적합도", direction="higher", unit="", algorithm="콘텐츠 기반"),
    ]
}


def criterion_values(
    p: Property, lifestyle: Lifestyle, travel: TravelTimes, features: PropertyFeatures
) -> dict[str, float | None]:
    """한 매물의 기준별 원래 값."""
    times = travel.get(p.property_id, {})
    return {
        "monthly_cost": monthly_fixed_cost(p),
        "weekly_commute": weekly_commute_min(times, lifestyle.timetable),
        "gap_home": gap_home_count(times, lifestyle.timetable, lifestyle.gap_buffer_min),
        "frequent_place": frequent_place_min(times, lifestyle.places),
        "late_return": late_return_min(times, lifestyle.places),
        "preference_match": preference_match(features.get(p.property_id, {}), lifestyle.preferences),
    }


# ---------------------------------------------------------------- 다기준 점수

def higher_is_better(values: dict[str, float]) -> dict[str, float]:
    """min-max, 가장 높은 값이 1. 모든 값이 같으면 전부 1.0 (lower_is_better 와 같은 규칙)."""
    return lower_is_better({k: -v for k, v in values.items()})


def normalize(values: dict[str, float], direction: Direction) -> dict[str, float]:
    return lower_is_better(values) if direction == "lower" else higher_is_better(values)


class Reason(BaseModel):
    """추천 근거 한 줄. LLM 이 이 데이터를 받아 문장으로 설명한다 (숫자는 LLM 이 만들지 않는다)."""

    criterion: str
    label: str
    value: float
    unit: str
    normalized: float  # 후보 중 상대 위치 (1 = 가장 좋음)


class StudentScored(BaseModel):
    rank: int
    property_id: str
    score: float
    contributions: dict[str, float]
    values: dict[str, float]
    over_budget: bool | None
    strengths: list[Reason]
    weaknesses: list[Reason]


class StudentExcluded(BaseModel):
    property_id: str
    reason: str


class StudentRecommendResult(BaseModel):
    weights: dict[str, float]  # 실제로 적용된 가중치 (합 1)
    top: list[StudentScored]
    ranked: list[StudentScored]
    excluded: list[StudentExcluded]
    notes: list[str]


def percent_to_weights(percent: Mapping[str, float], enabled: list[str], notes: list[str]) -> dict[str, float]:
    """사용자가 정한 반영 비율(%)을 켜진 기준의 가중치(합 1)로 바꾼다.

    입력이 없어 꺼진 기준의 비율은 남은 기준에 비율대로 나눈다 (notes 에 남김).
    비율을 정하지 않았으면 켜진 기준에 똑같이 나눈다.
    """
    if not percent:
        return {k: 1 / len(enabled) for k in enabled}
    kept = {k: percent.get(k, 0.0) for k in enabled}
    dropped = sum(percent.values()) - sum(kept.values())
    if dropped > 0:
        notes.append(f"계산하지 못한 기준의 비율 {dropped:g}% 는 나머지 기준에 비율대로 나눴습니다.")
    total = sum(kept.values())
    return {k: v / total for k, v in kept.items()}


STRENGTH_AT = 0.7  # 정규화 점수가 이 이상이면 장점
WEAKNESS_AT = 0.3  # 이 이하이면 단점


def recommend_for_student(
    properties: Iterable[Property],
    lifestyle: Lifestyle,
    *,
    travel: TravelTimes | None = None,
    features: PropertyFeatures | None = None,
    top_n: int = 3,
) -> StudentRecommendResult:
    """학생 맞춤 근거로 점수를 매긴다.

    - 생활 패턴에서 계산할 수 없는 기준(예: 시간표 미입력)은 모든 매물에서 끈다 (notes 에 남김).
    - 켜진 기준 중 값이 없는 매물은 제외한다 (excluded 에 이유).
    - 예산은 회의 결정 전이라 탈락시키지 않고 over_budget 표시만 한다.
    """
    properties = list(properties)
    travel = travel if travel is not None else travel_times_from_csv(properties)
    features = features or {}
    notes: list[str] = []
    excluded: list[StudentExcluded] = []

    raw = {p.property_id: criterion_values(p, lifestyle, travel, features) for p in properties}

    # 사용자가 0% 로 둔 기준과, 어떤 매물에서도 값이 안 나오는(입력 정보가 없는) 기준은 끈다
    percent = lifestyle.weights_percent
    has_input = {k for k in CRITERIA if any(v[k] is not None for v in raw.values())}
    enabled = [k for k in CRITERIA if k in has_input and (not percent or percent.get(k, 0) > 0)]
    for k in CRITERIA:
        if k not in has_input and (not percent or percent.get(k, 0) > 0):
            notes.append(f"'{CRITERIA[k].label}' 기준은 입력 정보가 없어 계산에서 뺐습니다.")
    if not enabled:
        return StudentRecommendResult(weights={}, top=[], ranked=[], excluded=[], notes=notes)

    valid: dict[str, dict[str, float]] = {}
    for p in properties:
        missing = [CRITERIA[k].label for k in enabled if raw[p.property_id][k] is None]
        if missing:
            excluded.append(StudentExcluded(property_id=p.property_id, reason=f"정보 없음: {', '.join(missing)}"))
            continue
        valid[p.property_id] = {k: raw[p.property_id][k] for k in enabled}  # type: ignore[misc]

    weights = percent_to_weights(percent, enabled, notes)

    norm = {k: normalize({pid: vals[k] for pid, vals in valid.items()}, CRITERIA[k].direction) for k in enabled}

    scored: list[StudentScored] = []
    for pid, vals in valid.items():
        contributions = {k: round(weights[k] * norm[k][pid], 4) for k in enabled}
        reasons = [
            Reason(
                criterion=k, label=CRITERIA[k].label, value=vals[k], unit=CRITERIA[k].unit,
                normalized=round(norm[k][pid], 4),
            )
            for k in enabled
        ]
        over_budget = None if lifestyle.budget_manwon is None else vals.get("monthly_cost", 0) > lifestyle.budget_manwon
        scored.append(
            StudentScored(
                rank=0,
                property_id=pid,
                score=round(sum(contributions.values()), 4),
                contributions=contributions,
                values=vals,
                over_budget=over_budget,
                # 가중치가 큰 기준부터 보여 준다
                strengths=sorted([r for r in reasons if r.normalized >= STRENGTH_AT], key=lambda r: -weights[r.criterion]),
                weaknesses=sorted([r for r in reasons if r.normalized <= WEAKNESS_AT], key=lambda r: -weights[r.criterion]),
            )
        )

    scored.sort(key=lambda s: (-s.score, s.property_id))
    ranked = [s.model_copy(update={"rank": i}) for i, s in enumerate(scored, start=1)]
    if len(ranked) < top_n:
        notes.append(f"추천 가능한 매물이 {len(ranked)}개뿐이라 TOP {top_n} 을 채우지 못했습니다.")

    return StudentRecommendResult(
        weights={k: round(w, 4) for k, w in weights.items()},
        top=ranked[:top_n],
        ranked=ranked,
        excluded=excluded,
        notes=notes,
    )
