"""챗봇 스키마 (통합안 추천 연결, agent-integrated-design.md 1장).

- AgentProfile: 대화로 모은 조건. POST /recommend 입력(RecommendRequest)과 같은 칸 이름을 쓰되 모두 '아직 모름'을 허용한다.
- TurnExtraction: LLM 이 한 턴의 메시지에서 뽑는 의도와 바꿀 조건. 서버(tools.apply_changes)가 다시 검증한다.
- PlanDraft: 제외 조건 계획(LLM 출력). 서버가 Condition 으로 바꾸며 검증한다 (tools.plan_from_draft).
- ChatRequest / ChatResponse: POST /agent/chat
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.exclusions import ExclusionPlan
from app.scoring import ClassSlot, Day, Factor, LargerArea

Intent = Literal[
    "update",            # 조건 입력·변경 → 다시 추천
    "recommend",         # 지금 조건으로 추천 (조건이 같이 오면 반영)
    "what_if",           # 가정: 조건은 그대로 두고 바꾼 조건으로 계산해 비교
    "revert",            # 이전 조건으로 되돌리기
    "compare",           # 지금 결과와 이전 결과 비교
    "explain",           # 결과·기준 설명
    "exclude",           # 제외 조건 추가 (5장)
    "remove_exclusion",  # 제외 조건 지우기
    "out_of_scope",      # 범위 밖 (7장)
    "greeting",          # 인사
]

ScopeTopic = Literal[
    "contract_legal",     # 계약·특약·보증금 반환·분쟁
    "fraud_registry",     # 매물 사기·안전 여부, 등기
    "landlord",           # 집주인·관리 상태
    "listing_guarantee",  # 매물 실재·현재 거래 가능 여부
    "market_forecast",    # 투자·시세 전망
    "unsupported_region", # 지원하지 않는 지역·학교
    "unrelated",          # 주거와 무관한 질문 (키·서버 정보 요청 포함)
    "timetable_photo",    # 시간표 사진·캡처를 읽어 달라는 요청 (R-202)
]


class AgentProfile(BaseModel):
    university: str | None = None
    department: str | None = None
    department_buildings: list[str] = Field(default_factory=list)
    priorities: list[Factor] = Field(default_factory=list, max_length=2, description="1·2순위 (3순위는 자동)")
    budget_manwon: Annotated[float | None, Field(gt=0)] = None
    deposit_budget_manwon: Annotated[float | None, Field(ge=0)] = None
    desired_commute_min: Annotated[float | None, Field(gt=0)] = None
    desired_area_m2: Annotated[float | None, Field(gt=0)] = None
    larger_area: LargerArea | None = Field(None, description="말하지 않으면 None (계산은 '상관없음', R-303)")
    timetable: list[ClassSlot] = Field(default_factory=list)
    exclusions: list[ExclusionPlan] = Field(default_factory=list)


class SlotIn(BaseModel):
    """LLM 출력용 시간표 칸. 빠진 칸은 null (서버가 빠진 칸만 묻는다, R-308)."""

    day: Day | None = None
    start: str | None = Field(None, description="HH:MM (24시간)")
    end: str | None = Field(None, description="HH:MM (24시간)")
    building: str | None = Field(None, description="사용자 표현 그대로. 말하지 않았으면 null")


class ProfileChanges(BaseModel):
    """이번 메시지에서 사용자가 실제로 말한 조건만. 말하지 않은 칸은 null. 숫자 계산(환산·더하기)은 하지 않는다."""

    university: str | None = Field(None, description="학교 이름 (사용자 표현 그대로 또는 정식 이름)")
    department: str | None = None
    department_buildings: list[str] | None = Field(None, description="학과 건물을 새로 정할 때 (사용자 표현 그대로)")
    add_department_buildings: list[str] | None = Field(None, description="'~에서도 수업 들어'처럼 건물을 더할 때")
    priorities: list[Factor] | None = Field(None, description="1·2순위를 둘 다 말했을 때 [1순위, 2순위]")
    priority_first: Factor | None = Field(None, description="1순위만 말했을 때 ('통학이 제일 중요해' → travel)")
    priority_second: Factor | None = Field(None, description="2순위만 말했을 때")
    budget_manwon: float | None = Field(None, description="유지비(월세+관리비) 상한, 만 원. 숫자를 그대로")
    budget_delta_manwon: float | None = Field(None, description="'10만 올려' → 10, '5만 줄여' → -5")
    deposit_budget_manwon: float | None = Field(None, description="보증금 상한, 만 원")
    clear_deposit_budget: bool = Field(False, description="'보증금은 상관없어' → true")
    desired_commute_min: float | None = Field(None, description="희망 통학 시간 (편도, 도보 분)")
    commute_delta_min: float | None = Field(None, description="'통학 5분 줄여' → -5")
    commute_not_walking: bool = Field(False, description="버스·지하철 등 도보가 아닌 시간으로 말했으면 true (그 숫자는 넣지 않는다)")
    area_value: float | None = Field(None, description="희망 면적 숫자 그대로 ('6평' → 6)")
    area_unit: Literal["㎡", "평"] | None = Field(None, description="area_value 의 단위")
    area_delta_m2: float | None = None
    larger_area: LargerArea | None = Field(None, description="넓은 면적: like 좋음 / neutral 상관없음 / dislike 싫음")
    timetable_add: list[SlotIn] | None = Field(None, description="대화로 말한 수업 칸 (요일마다 한 칸)")


class TurnExtraction(BaseModel):
    intent: Intent
    changes: ProfileChanges = Field(default_factory=ProfileChanges)
    exclusion_request: str | None = Field(
        None, description="제외 조건으로 넘길 사용자 기호 문장 (조건 변경으로 표현되지 않는 것만)")
    remove_exclusion: str | None = Field(None, description="지울 제외 조건 이름 ('술집'), 전부면 'all'")
    scope_topic: ScopeTopic | None = Field(None, description="intent=out_of_scope 일 때 종류")
    unsupported: list[str] = Field(default_factory=list, description="지금 반영할 수 없는 요청 (예: 알바하는 곳까지 거리)")
    property_rank: int | None = Field(None, description="'2등 매물'처럼 순위로 가리킨 매물")


# ---------------------------------------------------------------- 제외 조건 계획 (LLM 출력)

class ConditionDraft(BaseModel):
    tool: Literal["listing_field", "building_field", "amenity", "nearby_count", "nearest_distance"]
    label: str = Field(description="짧은 이름 (예: 술집, 편의점, 층 종류)")
    field: str | None = Field(None, description="listing_field / building_field 의 칸")
    amenity: str | None = Field(None, description="amenity 시설 키 (예: bar, convenience)")
    metric: Literal["nearest_m", "count_500m"] | None = None
    keywords: list[str] = Field(default_factory=list, description="카카오 검색어 (amenity 에 없는 장소만)")
    radius_m: int | None = None
    op: Literal[">", ">=", "<", "<=", "==", "!=", "in", "not_in"] = Field(description="이 조건이면 매물을 뺀다")
    value: float | None = Field(None, description="숫자 칸의 기준값")
    values: list[str] | None = Field(None, description="종류 칸(in / not_in)의 값 목록")


class PlanDraft(BaseModel):
    conditions: list[ConditionDraft] = Field(default_factory=list)
    unsupported: list[str] = Field(default_factory=list, description="데이터로 확인할 수 없는 기호")
    onsite_checks: list[str] = Field(default_factory=list, description="매물에서 직접 확인할 것 (정해진 항목 이름)")


# ---------------------------------------------------------------- 추천 결과 요약 (상태·응답용)

class TopItem(BaseModel):
    rank: int
    property_id: str
    score: float
    address: str | None = None
    property_type: str | None = None
    monthly_cost_manwon: float
    deposit_manwon: float | None
    area_m2: float
    over_budget: bool
    travel_avg_min: float | None = None
    source_url: str | None = None
    vs_target: list[str] = Field(default_factory=list, description="기준 대비 문장 (코드가 만든다, R-802)")


class RecSummary(BaseModel):
    university: str
    priorities: list[str]
    weights: dict[str, float]
    total_ranked: int
    excluded_count: int
    exclusion_counts: dict[str, int]
    top: list[TopItem]
    not_recommended: list[str] = Field(default_factory=list)
    ranks: dict[str, tuple[int, float]]
    notes: list[str]
    request: dict[str, Any]


class Comparison(BaseModel):
    label_before: str
    label_after: str
    total_before: int
    total_after: int
    top_before: list[str]
    top_after: list[str]
    entered_top: list[str]
    left_top: list[str]
    score_changes: list[dict[str, Any]]
    profile_diff: dict[str, Any]


# ---------------------------------------------------------------- API

Status = Literal["ok", "need_info", "blocked", "llm_unavailable", "llm_error", "error"]


class ChatRequest(BaseModel):
    session_id: Annotated[str, Field(min_length=1, max_length=100)]
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    profile: AgentProfile | None = Field(
        None, description="FE 에 저장된 생활 프로필 (있으면 이번 턴의 현재 조건으로 쓴다: 폼과 챗봇이 같은 조건)")


class ChatResponse(BaseModel):
    session_id: str
    assistant_message: str
    updated_profile: AgentProfile | None
    profile_changed: bool
    recommendation_results: RecSummary | None
    comparison_results: Comparison | None
    missing_fields: list[str] = Field(description="다음에 물어보는 칸 (최대 2개, R-601)")
    intent: str | None
    status: Status
    trace: dict[str, Any] | None = None
