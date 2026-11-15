"""에이전트 스키마 (통합안 엔진 기준, 2026-11-15 이전).

- AgentProfile: 대화로 모은 사용자 조건. 추천 요청(app.scoring.RecommendRequest, 통합안)과 같은 필드를 쓰되
  모두 '아직 모름'을 허용한다. 입력되지 않은 값은 만들지 않는다 (None / 빈 목록).
- TurnExtraction: LLM 이 한 턴의 메시지에서 뽑는 의도와 바꿀 조건 (구조화 출력 스키마). 서버가 다시 검증한다.
- ChatRequest / ChatResponse: POST /agent/chat
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.exclusions import ExclusionPlan
from app.frequent_places import MAX_PLACES, FrequentPlace
from app.scoring import ClassSlot, Day, Factor, LargerArea

# FE src/constants/universities.ts 와 같은 이름
UNIVERSITIES = ["이화여자대학교", "고려대학교", "서강대학교", "서울과학기술대학교"]

Intent = Literal[
    "recommend",         # 지금 조건으로 추천 (조건이 같이 들어오면 반영)
    "update",            # 조건을 바꾸고 다시 추천 (바뀐 조건을 저장)
    "what_if",           # 가정만 해 보고 비교 (지금 조건은 그대로)
    "revert",            # 바로 전 조건으로 되돌리기
    "compare_previous",  # 지금 결과와 바로 전 결과 비교
    "analyze",           # 의사결정 분석 (analysis 로 종류 지정)
    "policy",            # 주거 지원 정책 안내 (자격 판정 안 함, 2026-11-15 사용자 결정)
    "explain",           # 지난 추천 결과 설명 / 매물 질문
    "smalltalk",         # 그 밖의 대화
]

# 분석 도구 (Phase 2·7). 추천 점수·순위는 바꾸지 않는 참고 분석이다.
AnalysisKind = Literal[
    "pareto",       # 다른 매물보다 모든 면에서 못하지 않은 매물 (Pareto)
    "tradeoff",     # 돈을 더 내면 통학이 얼마나 줄어드는지 등
    "sensitivity",  # 조건을 조금 바꾸면 순위가 얼마나 흔들리는지
    "relaxation",   # 후보가 없거나 적을 때 어떤 조건을 풀면 되는지
    "market",       # 주변 전월세 실거래와 월세 비교 (매물 1개)
    "amenities",    # 주변 생활 편의시설 (매물 1개)
    "building",     # 건축물대장: 사용승인일·층수·승강기·주차·주용도 (매물 1개)
    "excluded",     # 추천에서 빠진 매물과 이유 (필터 단계별 수·예시 + 순위가 밀린 매물의 비추천 사유)
]

# 정책 질문 종류 (2026-11-15 예시 프롬프트 반영). 어느 것도 자격을 판정하지 않는다.
PolicyKind = Literal[
    "info",             # 정책 찾기·설명
    "eligibility",      # "내가 받을 수 있는 정책" → 판정하지 않고 공고 기준(연령·소득 표기)과 공식 링크만
    "compare",          # 정책 두 개 이상 나란히 (어느 쪽이 유리한지는 판단하지 않음)
    "apply",            # 신청 방법·서류·절차 (공식 데이터 표기 그대로)
    "contract_safety",  # 계약 전·당일·입주 후 점검 목록 (일반 안내, 공식 사이트 링크)
]


class AgentProfile(BaseModel):
    university: str | None = None
    budget_manwon: Annotated[float | None, Field(gt=0)] = None             # 유지비(월세+관리비) 상한
    deposit_budget_manwon: Annotated[float | None, Field(ge=0)] = None     # 보증금 상한 (선택)
    department: str | None = None
    department_buildings: list[str] = Field(default_factory=list)
    grade: Annotated[int | None, Field(ge=1)] = None                       # 받기만 하고 점수에 안 씀
    priorities: list[Factor] = Field(default_factory=list, max_length=2)  # 1·2순위 (3순위는 자동)
    desired_commute_min: Annotated[float | None, Field(gt=0)] = None       # 희망 통학 시간 (편도, 도보)
    desired_area_m2: Annotated[float | None, Field(gt=0)] = None
    larger_area: LargerArea | None = None                                  # 없으면 엔진 기본(상관없음)
    timetable: list[ClassSlot] = Field(default_factory=list)
    # 사용자가 승인한 제외 조건 (통합안 5장). 조건 하나 = 계획 하나 (지우기 쉽게)
    exclusions: list[ExclusionPlan] = Field(default_factory=list)
    # 자주 가는 곳 (챗봇이 카카오에서 찾은 곳, 2026-11-16). 추천 상위 매물만 도보 경로로 계산해 이동 시간에 넣는다
    frequent_places: list[FrequentPlace] = Field(default_factory=list, max_length=MAX_PLACES)


class SlotIn(BaseModel):
    """LLM 출력용 시간표 칸 (검증은 ClassSlot 으로 다시 한다)."""

    day: Day
    start: str = Field(description="HH:MM")
    end: str = Field(description="HH:MM")
    building: str


class PlacePref(BaseModel):
    """LLM 출력: 장소 기호 하나. 확인 계획(도구·반경·기준)은 서버가 만든다."""

    place: str = Field(description="사용자가 말한 장소 이름 그대로 (예: 술집, 편의점, 헬스장)")
    want: Literal["avoid", "near"] = Field(description="avoid: 근처에 없었으면 / near: 가까이 있었으면")
    radius_m: int | None = Field(None, description="사용자가 말한 거리(m). '5분 거리' 처럼 시간만 말하면 null")
    max_count: int | None = Field(None, description="avoid 에서 '2곳까지는 괜찮아' 처럼 허용 개수를 말했을 때")


class FrequentPlaceIn(BaseModel):
    """LLM 출력: 자주 가는 곳 하나. 위치는 서버가 카카오 장소 검색으로 찾는다."""

    query: str = Field(description="장소 검색어: 가게·역·건물 이름 부분만 (예: '신촌역', '스타벅스 신촌점', '이대역 헬스장')")
    name: str = Field(description="사용자 표현 그대로 (예: '알바하는 곳(신촌역 근처)')")
    visits_per_week: int = Field(description="주 몇 번 가는지. 말하지 않았으면 1")


class ProfileChanges(BaseModel):
    """이번 메시지에서 사용자가 실제로 말한 조건만. 말하지 않은 항목은 null."""

    university: str | None = Field(None, description=f"다음 중 하나: {', '.join(UNIVERSITIES)}")
    budget_manwon: float | None = Field(None, description="월세+관리비 상한 (만원). 절대값으로 말했을 때")
    budget_delta_manwon: float | None = Field(None, description="상한을 올리거나 내린 양 (만원, 내리면 음수). '10만 원 올리면' → 10")
    deposit_budget_manwon: float | None = Field(None, description="보증금 상한 (만원)")
    department: str | None = Field(None, description="학과·단과대 이름 (사용자 표현 그대로)")
    department_buildings: list[str] | None = Field(None, description="수업을 많이 듣는 건물 이름 (사용자 표현 그대로)")
    grade: int | None = None
    priorities: list[Factor] | None = Field(
        None, description="중요한 순서 (travel 이동 시간 / cost 예산 / area 면적). 1·2순위 전체를 준다. 순서만 바꿔도 전체")
    desired_commute_min: float | None = Field(None, description="희망 통학 시간 (편도, 분)")
    desired_area_m2: float | None = Field(None, description="희망 면적 (㎡). '5평' → 16.5")
    area_min_m2: float | None = Field(
        None, description="'20㎡ 이상만'처럼 꼭 지켜야 하는 최소 면적 (㎡, 필터). 희망 면적(점수 기준)과 다르다")
    larger_area: LargerArea | None = Field(None, description="희망보다 넓은 방: like 좋음 / neutral 상관없음 / dislike 싫음")
    timetable: list[SlotIn] | None = Field(None, description="시간표 전체 (바꿀 때는 바뀐 뒤의 전체)")
    place_prefs: list[PlacePref] | None = Field(None, description="새로 말한 장소 기호 (술집 근처 싫어, 편의점 가까이 등)")
    remove_exclusions: list[str] | None = Field(
        None, description="지울 장소 조건 이름 (예: ['술집']). 전부 지우면 ['*']")
    frequent_places: list[FrequentPlaceIn] | None = Field(
        None, description="새로 말한 자주 가는 곳 (알바·헬스장·학원·자주 가는 역 등, 거기까지 걷는 시간을 고려)")
    remove_frequent_places: list[str] | None = Field(
        None, description="지울 자주 가는 곳 (저장된 name 또는 장소 이름). 전부 지우면 ['*']")


class TurnExtraction(BaseModel):
    intent: Intent
    changes: ProfileChanges = Field(default_factory=ProfileChanges)
    analysis: AnalysisKind | None = Field(None, description="intent=analyze 일 때 분석 종류")
    unsupported: list[str] = Field(
        default_factory=list, description="지금 계산할 수 없는 요청 (예: 심야 안전, 오르막)")
    property_rank: int | None = Field(None, description="'2등 매물'처럼 순위로 가리킨 매물")
    policy_query: str | None = Field(None, description="intent=policy 일 때 찾을 정책 낱말 (예: '월세 지원', '전세 대출', '기숙사')")
    policy_kind: PolicyKind | None = Field(
        None, description="intent=policy 일 때 질문 종류: info / eligibility / compare / apply / contract_safety")
    policy_names: list[str] = Field(
        default_factory=list, description="compare 일 때 비교할 정책 이름들 (사용자 표현 그대로)")
    policy_regions: list[str] = Field(
        default_factory=list, description="사용자가 말한 지역 (예: ['서울', '서대문구']). 큰 단위부터")

    @field_validator("policy_names", "policy_regions", mode="before")
    @classmethod
    def _null_names(cls, v):
        return [] if v is None else v

    # 모델이 '바꿀 조건 없음'을 null 로 주는 경우 (EXAONE, 2026-11-15 평가에서 7건) → 빈 값으로 받는다
    @field_validator("changes", mode="before")
    @classmethod
    def _null_changes(cls, v):
        return {} if v is None else v

    @field_validator("unsupported", mode="before")
    @classmethod
    def _null_list(cls, v):
        return [] if v is None else v


# ---------------------------------------------------------------- 추천 결과 요약 (상태·응답용)

class TopItem(BaseModel):
    rank: int
    property_id: str
    score: float
    factor_scores: dict[str, float | None]
    contributions: dict[str, float]
    monthly_cost_manwon: float
    deposit_manwon: float | None
    area_m2: float
    travel_min: float | None          # 편도 평균 도보시간
    vs_target: dict[str, float | None]
    over_budget: bool
    address: str | None = None
    property_type: str | None = None
    source_url: str | None = None
    walk_min: dict[str, float] = Field(default_factory=dict)  # 요청에 쓴 건물까지 도보시간
    same_house_ids: list[str] = Field(default_factory=list)  # 같은 집일 수 있는 다른 광고 (TOP 에서는 묶어 하나만)
    frequent_walk_min: dict[str, float | None] = Field(default_factory=dict)  # 자주 가는 곳 이름 → 편도 도보(분), 못 구하면 None


class FrequentSummary(BaseModel):
    places: list[dict[str, Any]]   # 이름·카카오 장소 이름·주소·주간 횟수
    top_n: int                     # 다시 정렬한 상위 매물 수 (도보 경로 계산 범위)
    computed: int                  # 이번에 카카오로 계산
    cached: int                    # 저장된 결과 사용
    unknown: int                   # 계산 못 함 (경로 실패·좌표 없음·하루 상한) → 미확인
    limited: int                   # 그중 하루 상한 때문에 못 한 수


class RecSummary(BaseModel):
    university: str
    priorities: list[str]               # 1·2·3순위
    weights: dict[str, float]
    total_ranked: int
    excluded_count: int
    top: list[TopItem]
    ranks: dict[str, tuple[int, float | None]]  # 매물 ID → (순위, 점수), 비교용
    notes: list[str]
    request: dict[str, Any]  # 엔진에 넣은 요청 (재현·검증용)
    exclusion_counts: dict[str, int] = Field(default_factory=dict)  # 제외 조건 이름 → 빠진 매물 수
    frequent: FrequentSummary | None = None  # 자주 가는 곳 계산 범위·결과 (입력했을 때만)



class Comparison(BaseModel):
    label_before: str
    label_after: str
    total_before: int
    total_after: int
    top_before: list[str]
    top_after: list[str]
    entered_top: list[str]
    left_top: list[str]
    score_changes: list[dict[str, Any]]  # 두 결과 중 하나라도 TOP 인 매물의 순위·점수 변화
    profile_diff: dict[str, Any]


class AnalysisOut(BaseModel):
    kind: AnalysisKind
    property_id: str | None = None   # market·amenities 대상 매물
    result: dict[str, Any] | None    # 분석 API 응답과 같은 형식 (FE 표시용)


# ---------------------------------------------------------------- API

Status = Literal["ok", "need_info", "confirm", "llm_unavailable", "llm_error", "error"]


class ChatRequest(BaseModel):
    session_id: Annotated[str, Field(min_length=1, max_length=100)]
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    profile: AgentProfile | None = Field(
        None, description="FE 에 저장된 생활 프로필 (있으면 이번 턴의 현재 조건으로 쓴다: 폼과 챗봇이 같은 조건을 쓰게)")
    mode: Literal["recommend", "policy"] | None = Field(
        None, description="FE 모드 배너. recommend=매물 추천만, policy=정책 안내만 (조건 저장 안 함), 없으면 의도 자동 판별")


class ChatResponse(BaseModel):
    session_id: str
    assistant_message: str
    updated_profile: AgentProfile | None
    profile_changed: bool
    recommendation_results: RecSummary | None
    comparison_results: Comparison | None
    analysis_results: AnalysisOut | None = None
    policy_results: dict[str, Any] | None = Field(None, description="정책 안내 (app.policy.PolicyResult). 자격 판정 없음")
    missing_fields: list[str]
    intent: str | None
    status: Status
    trace: dict[str, Any] | None = Field(None, description="턴 기록: 의도, 거친 노드·시간, 도구, 사용 모델, 설명 출처")
    pending_change: dict[str, Any] | None = Field(None, description="status=confirm 일 때 바뀔 조건 (필드 → before/after)")


# ---------------------------------------------------------------- 시간표 사진 입력

ImageMime = Literal["image/png", "image/jpeg", "image/webp"]
MAX_IMAGE_B64 = 7_000_000  # base64 글자 수 (원본 약 5MB)


class SlotRead(BaseModel):
    """LLM 이 이미지에서 읽은 수업 칸 (건물은 이미지 표기 그대로)."""

    day: Day
    start: str = Field(description="HH:MM (24시간)")
    end: str = Field(description="HH:MM (24시간)")
    building_text: str = Field(description="강의실 표기에서 건물 부분 (예: 'ECC B146' → 'ECC'). 없으면 빈 문자열")
    course: str | None = Field(None, description="과목 이름 (보이면)")


class TimetableRead(BaseModel):
    slots: list[SlotRead] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="읽지 못했거나 애매한 칸")


class TimetableImageRequest(BaseModel):
    university: Annotated[str, Field(min_length=1)]
    image_base64: Annotated[str, Field(min_length=10, max_length=MAX_IMAGE_B64)]
    mime_type: ImageMime


class SlotOut(BaseModel):
    day: Day
    start: str
    end: str
    building: str | None   # 표준 건물 이름 (도보시간 데이터에 있는 건물). 못 맞추면 None → 사용자가 고른다
    building_text: str     # 이미지에서 읽은 표기
    course: str | None = None
    issue: str | None = None  # 시간 형식 오류, 모르는 건물 등


class TimetableImageResponse(BaseModel):
    status: Literal["ok", "llm_unavailable", "llm_error", "ocr_unavailable", "ocr_error"]
    message: str
    slots: list[SlotOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    buildings: list[str] = Field(default_factory=list)  # 고를 수 있는 건물 (확인 화면 선택지)
