"""추천 점수 v2 설정값 (설계안 recommend-score-design.md 8장).

추천 계산에 쓰는 숫자는 모두 이 파일에서 읽는다. 값은 전부 **초기값**이며
테스트 케이스로 조정한다. 바꿀 때는 이 파일만 고친다.

app/scoring.py 의 함수는 cfg 인자로 ScoringConfig 를 받는다 (기본값 CONFIG).
테스트에서 다른 값을 넣어 보려면 dataclasses.replace(CONFIG, 필드=값) 을 쓴다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringConfig:
    # ---- 3장 시간 점수 함수 score(t; T, h)
    score_max: float = 100.0         # 0분 (주거비는 필터 통과 매물 중 가장 싼 매물) 점수
    score_at_target: float = 85.0    # 희망 시간 지점 점수 (T분 = 85점)

    # ---- 2장 필수 조건 (예산 필터)
    # 2026-11-08 결정: 예산·보증금 예산은 1차 필터로만 쓴다 (여유 구간·'예산 초과' 없음, 점수에 안 씀).
    # 설계안 초기값은 월 +10만 원, 보증금 +20% 였다.
    monthly_budget_slack_manwon: float = 0.0   # 월 고정 주거비 > 예산 + 여유 → 제외
    deposit_budget_slack_ratio: float = 0.0    # 보증금 > 보증금 예산 × (1 + 여유) → 제외

    # ---- 4.3장 주거비 점수
    # 2026-11-08 결정: 예산과 상관없이 필터를 통과한 매물 안에서 상대 비교한다
    # (가장 싼 매물 100점 ~ 가장 비싼 매물 0점, 직선). 주거비만 절대 점수가 아니다.

    # ---- 4.1장 이동시간: 부분별 반감기 (T 는 학과 건물·시간표 모두 사용자 희망 통학 시간)
    department_half_life_min: float = 10.0
    timetable_half_life_min: float = 10.0
    frequent_target_min: float = 20.0      # 자주 가는 곳 T
    frequent_half_life_min: float = 15.0   # 자주 가는 곳 반감기

    # ---- 4.1장 이동시간 내부 비중 (입력·데이터가 없는 부분의 비중은 나머지에 비율대로 나눈다)
    travel_weight_department: float = 0.4
    travel_weight_frequent: float = 0.4
    travel_weight_timetable: float = 0.2

    # ---- 4.1장 대중교통 추정 (방법 A) — 10장 7단계에서 사용
    transit_compare_walk_min: float = 15.0  # 도보가 이보다 길면 min(도보, 대중교통 추정)
    transfer_penalty_min: float = 5.0       # 환승 1회당 보정
    wait_penalty_min: float = 3.0           # 대기 보정

    # ---- 4.2장 교통 편의성 — 10장 6단계에서 사용
    subway_target_min: float = 5.0
    subway_half_life_min: float = 5.0

    # ---- 5장 종합 점수: 1·2·3순위 가중치
    weight_rank1: float = 0.55
    weight_rank2: float = 0.30
    weight_rank3: float = 0.15

    # ---- 6장 결과 표시
    result_limit: int = 50              # 추천 목록 최대 개수 (임시)
    displayed_top_n: int = 3            # "지금 화면에 있는 매물" = 현재 조합 상위 N위. 화면 개수가 정해지면 바꾼다
    not_recommended_top_k: int = 3      # 다른 조합에서 상위 K위 안이면 비추천 사유 후보
    not_recommended_max: int = 3        # 비추천 사유 최대 개수

    # ---- 출력 반올림 자릿수 (계산은 반올림 없이 하고, 응답에 넣을 때만 반올림)
    score_decimals: int = 2
    minutes_decimals: int = 1
    money_decimals: int = 2


CONFIG = ScoringConfig()


# 학과 → 학과 건물 (1장: 학과를 고르면 학과 건물을 자동으로 채우고, 사용자가 고칠 수 있다).
# ⚠️ 예시값, 팀 확인 필요. 실제 학과별 건물 데이터가 아직 없어 화면·테스트용 예시만 넣었다.
#    건물 이름은 DB/poc_ewha.csv 의 강의동 4개(ECC, 이화·포스코관, 학관, 공과대학) 중에서만 쓴다.
DEPARTMENT_BUILDINGS: dict[str, tuple[str, ...]] = {
    "컴퓨터공학전공": ("공과대학",),        # 예시값, 팀 확인 필요
    "경영학부": ("이화·포스코관",),          # 예시값, 팀 확인 필요
    "국어국문학과": ("학관", "ECC"),         # 예시값, 팀 확인 필요 (여러 건물을 쓰는 학과 예시)
}
