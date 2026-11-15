"""의사결정 지원 분석 API (Phase 2). 추천 엔진 결과는 그대로 두고 분석 결과만 따로 돌려준다.

- POST /analysis/pareto      : Pareto Frontier (유지비 · 보증금 · 통학 · 면적)
- POST /analysis/tradeoff    : 비용 ↔ 통학 ↔ 면적 Trade-off
- POST /analysis/sensitivity : 우선순위·가중치·예산·기준값을 바꿨을 때 순위 변화
- POST /analysis/relaxation  : 후보가 없거나 적을 때 조건 완화 가정
"""

from fastapi import APIRouter, HTTPException

from app.decision.pareto import ParetoRequest, ParetoResult, TradeoffRequest, TradeoffResult, run_pareto, run_tradeoff
from app.decision.relaxation import RelaxationRequest, RelaxationResult, run_relaxation
from app.decision.sensitivity import SensitivityRequest, SensitivityResult, run_sensitivity
from app.scoring import UnknownBuildingError

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _guard(fn, body):
    try:
        return fn(body)
    except UnknownBuildingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/pareto", response_model=ParetoResult)
def pareto(body: ParetoRequest) -> ParetoResult:
    return _guard(run_pareto, body)


@router.post("/tradeoff", response_model=TradeoffResult | None)
def tradeoff(body: TradeoffRequest) -> TradeoffResult | None:
    return _guard(run_tradeoff, body)


@router.post("/sensitivity", response_model=SensitivityResult)
def sensitivity(body: SensitivityRequest) -> SensitivityResult:
    return _guard(run_sensitivity, body)


@router.post("/relaxation", response_model=RelaxationResult)
def relaxation(body: RelaxationRequest) -> RelaxationResult:
    return _guard(run_relaxation, body)
