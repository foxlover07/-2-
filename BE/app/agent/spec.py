"""추천 명세 (에이전트용) — 코드에서 자동으로 만든다 (통합안 엔진 기준).

문서(docs/*.md)를 프롬프트에 붙이지 않는다: 코드가 바뀌면 문서와 어긋나고, 길면 모델 정확도가 떨어진다.
여기서는 실제 설정값(ScoringConfig)과 실제 데이터(대학별 매물·건물)를 읽어 짧게 만든다.

- 조건 추출: 어떤 요소가 있고 무엇이 필요한지 알아야 정확히 뽑는다.
- 설명: 점수가 어떻게 나오는지 알아야 "왜 1위인지"를 말할 수 있다. 계산은 하지 않는다 (엔진).
"""

from __future__ import annotations

from app import frequent_places as fp
from app.agent import tools
from app.agent.schemas import UNIVERSITIES
from app.scoring import FACTOR_LABELS
from app.scoring_config import CONFIG

MAX_BUILDINGS = 12


def _g(x: float) -> str:
    return f"{x:g}"


def capability_spec(university: str | None = None) -> str:
    """에이전트에게 주는 추천 명세 (한국어, 짧게)."""
    c = CONFIG
    w = (c.weight_rank1, c.weight_rank2, c.weight_rank3)
    lines = [
        "[추천 방식 — 서비스 코드가 계산, 너는 계산하지 않는다]",
        f"1) 필터(점수에는 안 씀): 유지비(월세+관리비)가 상한 + {_g(c.monthly_budget_slack_manwon)}만 원을 넘으면 제외 "
        f"(상한~상한+{_g(c.monthly_budget_slack_manwon)}만 원은 '예산 초과' 표시). 보증금 상한을 주면 "
        f"상한 × {_g(1 + c.deposit_budget_slack_ratio)} 를 넘는 매물 제외. 월세·관리비·면적·도보시간이 없는 매물은 제외.",
        f"2) 요소 3개: {', '.join(f'{k}({v})' for k, v in FACTOR_LABELS.items())}. 각 요소는 사용자 기준에서 "
        f"{_g(c.base_score)}점, 좋은 쪽 단위당 +{_g(c.good_slope)}, 나쁜 쪽 단위당 -{_g(c.bad_slope)} (0~100). "
        f"단위(교환비): 편도 {_g(c.unit_travel_min)}분 = 월 {_g(c.unit_cost_manwon)}만 원 = {_g(c.unit_area_m2)}㎡.",
        "   이동 시간 = 학과 건물 평균 편도 도보시간(+ 시간표가 있으면 수업 건물 이동). 예산 = 유지비 vs 상한. "
        "면적 = 희망 면적 대비 (희망보다 넓은 쪽은 larger_area 선택: 좋음/상관없음/싫음).",
        f"   자주 가는 곳(선택, 최대 {fp.MAX_PLACES}곳): 학교 {fp.SEARCH_RADIUS_M // 1000}km 안 장소만 카카오에서 찾아, "
        f"1차 순위 상위 {fp.TOP_N}개 매물만 도보 경로를 계산하고 그 안에서 다시 정렬 (주 방문 횟수로 가중). "
        "못 구한 매물은 '미확인'.",
        f"3) 1·2순위를 사용자가 고르고 3순위는 남은 요소. 순위별 비중 {', '.join(f'{round(x * 100)}%' for x in w)} 고정 "
        "(사용자가 비중 숫자를 정할 수 없다).",
        f"4) 필터를 통과한 매물을 모두 순위로 보여 주고 상위 {c.displayed_top_n}개를 강조. 점수가 같으면 유지비 낮은 순.",
        "5) 필수: 학교, 월 유지비 상한, 1·2순위, 희망 통학 시간(편도 분), 희망 면적(㎡), 수업 듣는 학과 건물. 보증금 상한·시간표는 선택.",
        "[참고 분석 — 순위는 바꾸지 않는다]",
        "- pareto: 모든 면(유지비·보증금·통학·면적)에서 다른 매물보다 못하지 않은 매물",
        "- tradeoff: 돈을 더 내면 통학이 얼마나 줄어드는지, 비슷한 통학에서 더 싼 매물",
        "- sensitivity: 우선순위·가중치·예산·기준을 조금 바꿨을 때 상위 매물이 얼마나 유지되는지",
        "- relaxation: 후보가 없거나 적을 때 어떤 조건을 얼마나 풀면 되는지 (가정)",
        "- market: 추천 매물 월세 vs 주변 전월세 실거래(국토부, 과거 계약). 관리비는 실거래에 없어 따로",
        "- amenities: 추천 매물 주변 편의점·마트·세탁소·약국·의원 등 직선거리와 개수 (상가정보)",
        "- building: 추천 매물 지번의 건축물대장 (사용승인일·층수·승강기·주차·주용도). 광고 지번 기준이라 실제 건물과 다를 수 있음",
    ]
    lines.append("[정책 안내] 주거 지원 정책은 공식 데이터(온통청년)를 찾아 출처·기준일·링크와 함께 안내만 한다. "
                 "자격 판정·대상 여부 판단은 하지 않고 공식 사이트로 안내한다. 지원 금액을 추천 점수에 넣지 않는다.")
    counts = {u: tools.university_listing_count(u) for u in UNIVERSITIES}
    lines.append("[데이터]")
    lines.append("- 매물이 있는 대학: " + ", ".join(f"{u} {n}건" for u, n in counts.items() if n) +
                 ("; 매물 없음: " + ", ".join(u for u, n in counts.items() if not n) if any(not n for n in counts.values()) else ""))
    if university and counts.get(university):
        b = tools.usable_buildings(university)
        lines.append(f"- {university}에서 고를 수 있는 건물: {', '.join(b[:MAX_BUILDINGS])}{' 등' if len(b) > MAX_BUILDINGS else ''}")
    lines.append("- 도보시간은 Kakao 도보 경로(건물 대표 지점) 기준, 대중교통은 반영하지 않는다. 매물은 광고 매물(입주 가능 미확인).")
    return "\n".join(lines)
