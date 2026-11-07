"""추천 점수 v2 (설계안 recommend-score-design.md).

요소는 이동시간 · 교통 편의성 · 주거비 3개이고, 각 요소는 매물별 절대 점수(0~100)다.
다른 매물에 따라 점수가 바뀌지 않는다 (min-max 정규화를 쓰지 않는다).
숫자는 모두 app/scoring_config.py 에서 읽는다. 계산은 전부 코드가 하고 LLM 은 쓰지 않는다.
"""

from app.scoring_config import CONFIG, ScoringConfig


# ---------------------------------------------------------------- 3장 시간 점수 함수

def time_score(t: float, target: float, half_life: float, cfg: ScoringConfig = CONFIG) -> float:
    """설계안의 score(t; T, h). 편도 t분 → 0~100 점.

    t ≤ T : score_max − (score_max − score_at_target) × t / T   (기본 100 − 15·t/T, 완만한 직선)
    t > T : score_at_target × 2^(−(t − T) / h)                  (기본 85 × 2^(−(t−T)/h), h분마다 절반)

    0분 = 100점, T분 = 85점. 0에 가까워지기만 하고 0이 되지는 않는다.
    """
    if t < 0:
        raise ValueError(f"시간은 0 이상이어야 합니다: {t}")
    if target <= 0 or half_life <= 0:
        raise ValueError(f"기준 시간과 반감기는 0보다 커야 합니다: T={target}, h={half_life}")
    if t <= target:
        return cfg.score_max - (cfg.score_max - cfg.score_at_target) * t / target
    return cfg.score_at_target * 2 ** (-(t - target) / half_life)
