"""주거 지원 정책 **안내** (2026-11-15 사용자 결정, CLAUDE.md 0-7).

- 공식 데이터(온통청년 청년정책 API, pipeline.collect_policies 결과)의 정책 내용을 출처·기준일·공식 링크와 함께 보여 준다.
- 자격을 판정하지 않는다 ("대상자입니다" 금지). 지원 금액을 추천 점수·유지비 계산에 넣지 않는다.
- 데이터가 없으면(키 발급 전) 공식 사이트 링크만 안내한다. 정책 내용을 만들어내지 않는다.

데이터: DB/staging/policies_*.json 중 가장 최근 파일 (POLICIES_PATH 로 바꿀 수 있음). 형식은 PolicyItem 목록.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import REPO_ROOT, _read

SOURCE = "온통청년 청년정책 OPEN API (한국고용정보원)"
OFFICIAL_LINKS = [
    {"name": "온통청년 (청년정책 통합 검색)", "url": "https://www.youthcenter.go.kr"},
    {"name": "마이홈포털 (공공임대·주거지원)", "url": "https://www.myhome.go.kr"},
]
DISCLAIMER = "정책 안내용 정보입니다. 지원 자격과 금액은 개인 상황에 따라 다르니 공식 공고와 신청 사이트에서 확인하세요."


class PolicyItem(BaseModel):
    policy_id: str
    name: str
    summary: str | None = None            # 정책 설명
    support: str | None = None            # 지원 내용
    category: str | None = None           # 대분류 (주거 등)
    subcategory: str | None = None        # 중분류 (전월세 및 주거급여 지원 등)
    keywords: list[str] = Field(default_factory=list)
    agency: str | None = None             # 주관·운영 기관
    region: str | None = None             # 지역 (전국/서울 등, 원본 표기 그대로)
    target_age: str | None = None         # 원본의 연령 조건 표기 (판정에 쓰지 않음)
    apply_period: str | None = None       # 신청 기간 표기 (상시 등)
    apply_url: str | None = None
    ref_url: str | None = None
    updated_at: str | None = None         # 원본의 최종 수정일
    fetched_at: str | None = None


class PolicyResult(BaseModel):
    available: bool
    query: str
    items: list[PolicyItem] = Field(default_factory=list)
    total_matched: int = 0
    reason: str | None = None
    source: str = SOURCE
    fetched_at: str | None = None
    official_links: list[dict] = Field(default_factory=lambda: list(OFFICIAL_LINKS))
    disclaimer: str = DISCLAIMER


def _path() -> Path | None:
    override = (_read("POLICIES_PATH") or "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (REPO_ROOT / "BE" / p).resolve()
    files = sorted((REPO_ROOT / "DB" / "staging").glob("policies_*.json"))
    return files[-1] if files else None


@lru_cache
def load_policies() -> tuple[PolicyItem, ...]:
    p = _path()
    if p is None or not p.exists():
        return ()
    data = json.loads(p.read_text(encoding="utf-8"))
    return tuple(PolicyItem.model_validate(x) for x in data.get("items", []))


_SPLIT = re.compile(r"[\s,·/]+")
# 사용자 표현 → 원본에 쓰이는 낱말 (검색만 넓힌다. 판정에는 쓰지 않음)
SYNONYMS = {"월세": ["월세", "임차", "주거비"], "전세": ["전세", "보증금", "임차"], "대출": ["대출", "융자", "이자"],
            "기숙사": ["기숙사", "연합기숙사"], "공공임대": ["공공임대", "임대주택", "행복주택", "매입임대"],
            "이사": ["이사", "중개", "중개보수"]}


def search_policies(query: str = "", region: str | None = None, limit: int = 5,
                    items: tuple[PolicyItem, ...] | None = None) -> PolicyResult:
    """주거 정책 검색 (낱말 일치 점수, 이름·키워드 가중). 자격 조건으로 거르지 않는다."""
    items = load_policies() if items is None else items
    if not items:
        return PolicyResult(available=False, query=query,
                            reason="정책 데이터를 아직 받지 않았습니다 (온통청년 OPEN API 키 발급 대기). 공식 사이트에서 확인해 주세요.")
    words: list[str] = []
    for w in _SPLIT.split(query or ""):
        w = w.strip()
        if len(w) >= 2:
            words += SYNONYMS.get(w, [w])
    words = list(dict.fromkeys(words))

    def score(it: PolicyItem) -> int:
        head = f"{it.name} {' '.join(it.keywords)} {it.subcategory or ''}"
        body = f"{it.summary or ''} {it.support or ''}"
        return sum(3 * head.count(w) + body.count(w) for w in words)

    pool = [it for it in items if not region or not it.region or region in it.region or "전국" in it.region]
    ranked = sorted(pool, key=lambda it: (-score(it), it.name)) if words else sorted(pool, key=lambda it: it.name)
    if words:
        ranked = [it for it in ranked if score(it) > 0] or ranked
    fetched = max((it.fetched_at for it in items if it.fetched_at), default=None)
    return PolicyResult(available=True, query=query, items=ranked[:limit], total_matched=len(ranked), fetched_at=fetched)
