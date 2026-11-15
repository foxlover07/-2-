"""추천 엔진 호출 공통부. 분석 모듈은 이 함수로만 엔진을 부른다 (엔진 코드는 그대로)."""

from __future__ import annotations

from collections.abc import Sequence

from app.data import load_properties, load_travel_times
from app.models import Property
from app.scoring import CONFIG, RankedProperty, RecommendRequest, RecommendResult, recommend
from app.scoring_config import ScoringConfig


class EngineContext:
    """한 번의 분석에서 매물·도보시간을 한 번만 읽고 여러 가정 조건으로 엔진을 부른다."""

    def __init__(self, properties: Sequence[Property] | None = None, travel=None):
        self.properties = list(properties) if properties is not None else list(load_properties())
        self.travel = travel if travel is not None else load_travel_times(self.properties)
        self.calls = 0

    def run(self, req: RecommendRequest, cfg: ScoringConfig = CONFIG) -> RecommendResult:
        self.calls += 1
        # 챗봇 장소 조건은 상가정보(로컬)로만 확인한다. 카카오는 분석에서 부르지 않는다 (없으면 엔진이 notes 로 안내)
        if req.exclusions and not hasattr(self, "_searcher"):
            from app.place_search import place_searcher
            self._searcher = place_searcher()
        return recommend(self.properties, req, travel=self.travel, cfg=cfg,
                         searcher=getattr(self, "_searcher", None) if req.exclusions else None)


def travel_min(r: RankedProperty) -> float | None:
    """편도 평균 도보시간(분) = 희망 통학 시간 + 기준 대비 차이. 엔진이 응답에 넣은 값에서 되돌린다."""
    if r.vs_target.travel_min is None:
        return None
    target = next(iter(r.travel_parts.values())).target_min if r.travel_parts else None
    return None if target is None else round(target + r.vs_target.travel_min, 1)


def summary(r: RankedProperty) -> dict:
    return {
        "property_id": r.property_id,
        "rank": r.rank,
        "score": r.score,
        "monthly_cost_manwon": r.monthly_cost_manwon,
        "deposit_manwon": r.deposit_manwon,
        "travel_min": travel_min(r),
        "area_m2": r.area_m2,
        "over_budget": r.over_budget,
    }
