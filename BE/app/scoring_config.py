"""추천 점수 설정값 (추천 시스템 통합안 2026-11-13, 6장).

추천 계산에 쓰는 숫자는 모두 이 파일에서 읽는다. 값은 전부 **초기값**이며
테스트 케이스로 조정한다. 바꿀 때는 이 파일만 고친다.

app/scoring.py 의 함수는 cfg 인자로 ScoringConfig 를 받는다 (기본값 CONFIG).
테스트에서 다른 값을 넣어 보려면 dataclasses.replace(CONFIG, 필드=값) 을 쓴다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringConfig:
    # ---- 2장 필터 (여유 있는 예산)
    monthly_budget_slack_manwon: float = 10.0  # 유지비 > 상한 + 10만 원 → 제외. 상한 ~ 상한 + 10 은 '예산 초과' 표시
    deposit_budget_slack_ratio: float = 0.2    # 보증금 > 보증금 상한 × (1 + 0.2) → 제외

    # ---- 3.1장 공통 규칙: 기준 50점, 좋은 쪽 단위당 +5, 나쁜 쪽 단위당 −10, 0~100 으로 자름
    score_min: float = 0.0
    score_max: float = 100.0
    base_score: float = 50.0
    good_slope: float = 5.0
    bad_slope: float = 10.0

    # ---- 3.1장 교환비 (단위): 편도 10분 = 월 5만 원 = 2㎡  (3.2장: 매물 1,175개 회귀분석 근거)
    unit_travel_min: float = 10.0
    unit_cost_manwon: float = 5.0
    unit_area_m2: float = 2.0

    # ---- 3.3장 면적: 희망보다 넓은 쪽의 단위당 점수 (larger_area 선택)
    larger_area_like_slope: float = 5.0       # 좋음: 넓을수록 +5점/2㎡
    larger_area_neutral_slope: float = 0.0    # 상관없음: 50점 유지
    larger_area_dislike_slope: float = -2.5   # 싫음: 넓을수록 −2.5점/2㎡ (완만한 감점)

    # ---- 3.3장 이동 시간 내부 비중 (입력·데이터가 없는 부분의 비중은 나머지에 비율대로 나눈다)
    travel_weight_department: float = 0.4
    travel_weight_frequent: float = 0.4
    travel_weight_timetable: float = 0.2

    # ---- 4.1장 종합 점수: 1·2·3순위 가중치
    weight_rank1: float = 0.55
    weight_rank2: float = 0.30
    weight_rank3: float = 0.15

    # ---- 4.2장 결과
    displayed_top_n: int = 3            # "지금 상위 N위" (비추천 사유는 이 밖의 매물). 화면 개수가 정해지면 바꾼다
    not_recommended_top_k: int = 3      # 다른 조합에서 상위 K위 안이면 비추천 사유 후보
    not_recommended_max: int = 3        # 비추천 사유 최대 개수

    # ---- 5장 챗봇 제외 조건 (확인 도구). ⚠️ 챗봇은 미완성 — 아래는 도구 실행용 초기값
    kakao_call_limit: int = 500             # 추천 한 번에 카카오 호출 상한
    nearest_search_radius_m: int = 1000     # nearest_distance 가 찾는 최대 반경 (이 안에 없으면 '없음')
    kakao_max_pages: int = 3                # nearby_count 키워드 하나당 최대 페이지 (카카오는 최대 45개까지 준다)
    kakao_page_size: int = 15

    # ---- 출력 반올림 자릿수 (계산은 반올림 없이 하고, 응답에 넣을 때만 반올림)
    score_decimals: int = 2
    minutes_decimals: int = 1
    money_decimals: int = 2
    area_decimals: int = 2


CONFIG = ScoringConfig()

EWHA = "이화여자대학교"

# 학과 → 학과 건물 (1장: 이대는 학과를 고르면 학과 건물을 자동으로 채우고, 사용자가 고칠 수 있다).
# ⚠️ 예시값, 팀 확인 필요. 실제 학과별 건물 데이터가 아직 없어 화면·테스트용 예시만 넣었다.
#    건물 이름은 DB/poc_ewha.csv 의 강의동 4개(ECC, 이화·포스코관, 학관, 공과대학) 중에서만 쓴다.
#    다른 대학은 표가 없어 사용자가 건물을 고른다.
DEPARTMENT_BUILDINGS: dict[str, dict[str, tuple[str, ...]]] = {
    EWHA: {
        "컴퓨터공학전공": ("공과대학",),        # 예시값, 팀 확인 필요
        "경영학부": ("이화·포스코관",),          # 예시값, 팀 확인 필요
        "국어국문학과": ("학관", "ECC"),         # 예시값, 팀 확인 필요 (여러 건물을 쓰는 학과 예시)
    },
}
