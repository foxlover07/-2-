"""추천 API (설계안 v2). LLM 없이 입력 JSON → 결과.

- POST /recommend : app.scoring.RecommendRequest → app.scoring.recommend

이동시간은 서버에 있는 DB/poc_ewha.csv 의 강의동 4개(spot1~4) 도보시간을 쓴다.
(Kakao 36개 건물 경로 데이터는 공개 저장소에 두지 않으므로 배포본에는 없다.)
"""

from fastapi import APIRouter, HTTPException

from app.data import load_properties
from app.scoring import RecommendRequest, RecommendResult, UnknownBuildingError, recommend

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendResult)
def recommend_v2(req: RecommendRequest) -> RecommendResult:
    try:
        return recommend(load_properties(), req)
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
