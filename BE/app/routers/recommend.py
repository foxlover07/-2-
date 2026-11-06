"""추천 API. 팀의 MCDM(가중합) 계산을 그대로 노출한다 — PBO 도입 전 baseline.

- POST /recommend          : FE Profile 모양(visits·budget·importance) → app.recommend.recommend
- POST /recommend/student  : 시간표·자주 가는 곳·반영 비율 → app.criteria.recommend_for_student

이동시간은 서버에 있는 DB/poc_ewha.csv 의 spot1~4 도보시간을 쓴다.
(Kakao 36개 건물 경로 데이터는 공개 저장소에 두지 않으므로 배포본에는 없다.)
"""

from fastapi import APIRouter, Query

from app.criteria import Lifestyle, StudentRecommendResult, recommend_for_student
from app.data import load_properties
from app.recommend import DEFAULT_TOP_N, BudgetMode, Profile, RecommendResult, recommend

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendResult)
def recommend_basic(
    profile: Profile,
    top_n: int = Query(DEFAULT_TOP_N, ge=1, le=10),
    budget_mode: BudgetMode = "exclude",
) -> RecommendResult:
    return recommend(load_properties(), profile, top_n=top_n, budget_mode=budget_mode)


@router.post("/student", response_model=StudentRecommendResult)
def recommend_student(
    lifestyle: Lifestyle,
    top_n: int = Query(DEFAULT_TOP_N, ge=1, le=10),
) -> StudentRecommendResult:
    return recommend_for_student(load_properties(), lifestyle, top_n=top_n)
