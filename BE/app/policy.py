"""주거 지원 정책 **안내** (2026-11-15 사용자 결정, CLAUDE.md 0-7).

- 공식 데이터(온통청년 청년정책 API, pipeline.collect_policies 결과)의 정책 내용을 출처·기준일·공식 링크와 함께 보여 준다.
- 자격을 판정하지 않는다 ("대상자입니다" 금지). 지원 금액을 추천 점수·유지비 계산에 넣지 않는다.
- 데이터가 없으면(키 발급 전) 공식 사이트 링크만 안내한다. 정책 내용을 만들어내지 않는다.

데이터: DB/staging/policies_*.json 중 가장 최근 파일 (POLICIES_PATH 로 바꿀 수 있음). 형식은 PolicyItem 목록.
API 데이터가 없으면 DB/reference/policies_curated.json (2026-11-16, 공식 페이지를 직접 확인해 옮긴 임시 목록, 항목별 ref_url·확인일).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import REPO_ROOT, _read

SOURCE = "온통청년 청년정책 OPEN API (한국고용정보원)"
CURATED_SOURCE = "각 정책 공식 페이지 (서비스가 직접 확인해 옮겨 적은 임시 목록, 온통청년 API 데이터가 생기면 대체)"
OFFICIAL_LINKS = [
    {"name": "온통청년 (청년정책 통합 검색)", "url": "https://www.youthcenter.go.kr"},
    {"name": "마이홈포털 (공공임대·주거지원)", "url": "https://www.myhome.go.kr"},
    {"name": "주택도시기금 (버팀목 등 전월세 대출)", "url": "https://nhuf.molit.go.kr"},
]
DISCLAIMER = "정책 안내용 정보입니다. 지원 자격과 금액은 개인 상황에 따라 다르니 공식 공고와 신청 사이트에서 확인하세요."

# 질문 종류별 안내 (자격·유불리를 판단하지 않는다는 점을 서버 문장으로 고정)
KIND_NOTES = {
    "eligibility": "말씀하신 나이·소득·거주 형태로 자격을 판정하지 않아요. 각 정책의 공고 기준(연령·소득 표기)을 보고 "
                   "공식 사이트에서 확인해 주세요. 입력하신 개인 정보는 저장하지 않습니다.",
    "compare": "어느 정책이 더 유리한지는 소득·보증금·금리·심사 결과에 따라 달라 판단하지 않아요. 공식 데이터의 내용을 나란히 보여 드립니다.",
    "apply": "신청 방법·서류는 공식 데이터에 적힌 그대로이며 공고마다 바뀔 수 있어요. 신청 전에 공식 페이지에서 최신 공고를 확인해 주세요.",
    "region": "구 단위 정책은 온통청년 데이터에 없을 수 있어요. 해당 시·구청 누리집도 함께 확인해 주세요.",
}

# 계약 안전 점검 목록 (일반 안내). 법률 자문이 아니며 공식 사이트의 절차가 우선한다.
# 내용은 공식 기관이 안내하는 일반적인 확인 항목을 서비스가 정리한 것 (2026-11-15 작성, 특정 문서 원문 대조는 미검증).
CHECKLIST_SOURCE = "서비스가 정리한 일반 점검 목록 (2026-11-15 작성). 법률 자문이 아니며, 절차·제도는 공식 사이트 안내가 우선합니다."
CONTRACT_CHECKLIST: list[dict] = [
    {"phase": "계약 전", "items": [
        {"text": "등기사항전부증명서(등기부등본)를 직접 발급해 소유자가 계약 상대와 같은지, 근저당·압류·가압류·신탁 같은 권리 관계가 있는지 확인",
         "link": {"name": "인터넷등기소", "url": "https://www.iros.go.kr"}},
        {"key": "building", "text": "건축물대장에서 건물 용도와 위반건축물 표시 확인 (주거용이 아닌 근린생활시설 등은 전입신고·보증보험 가입이 어려울 수 있음)",
         "link": {"name": "세움터", "url": "https://www.eais.go.kr"}},
        {"key": "market", "text": "보증금·월세가 주변 시세보다 지나치게 높지 않은지 실거래가로 확인",
         "link": {"name": "국토교통부 실거래가 공개시스템", "url": "https://rt.molit.go.kr"}},
        {"key": "multi_family", "text": "다가구주택이면 먼저 들어온 세입자들의 보증금(선순위 보증금) 규모를 임대인에게 확인 요청", "link": None},
        {"text": "임대인의 국세·지방세 체납 여부 확인 (납세증명서 요청 등)", "link": None},
        {"text": "중개사무소가 관할 시·군·구에 등록된 곳인지 확인", "link": None},
    ]},
    {"phase": "계약 당일", "items": [
        {"text": "계약 직전에 등기부를 다시 발급해 그사이 권리 관계가 바뀌지 않았는지 확인",
         "link": {"name": "인터넷등기소", "url": "https://www.iros.go.kr"}},
        {"text": "신분증으로 등기부상 소유자 본인인지 확인하고, 대리인이면 위임장과 인감증명서 확인", "link": None},
        {"text": "계약금·보증금은 등기부상 소유자 명의 계좌로 보내고 송금 기록 남기기", "link": None},
        {"text": "필요한 특약(예: 잔금일까지 담보 설정 등 권리 변동을 하지 않는다)을 계약서에 적을지 중개사와 상의", "link": None},
    ]},
    {"phase": "입주 후", "items": [
        {"text": "입주(잔금) 당일 전입신고와 확정일자 받기 — 대항력은 입주와 전입신고를 마친 다음 날 0시부터 생김",
         "link": {"name": "정부24", "url": "https://www.gov.kr"}},
        {"key": "deposit", "text": "전세보증금 반환보증 가입 여부 검토", "link": {"name": "주택도시보증공사(HUG)", "url": "https://www.khug.or.kr"}},
        {"text": "계약서·영수증·송금 기록 보관", "link": None},
    ]},
]


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
    income_text: str | None = None        # 원본의 소득 조건 표기 (판정에 쓰지 않음)
    apply_method: str | None = None       # 신청 방법·절차 표기
    documents: str | None = None          # 제출 서류 표기
    screening: str | None = None          # 심사·발표 방법 표기
    apply_url: str | None = None
    ref_url: str | None = None
    updated_at: str | None = None         # 원본의 최종 수정일
    fetched_at: str | None = None
    status_note: str | None = None        # 임시 목록: 공고 연도·확인 필요 사항 (원본 페이지 기준)
    # 주거 형태 분류 (load 시 classify_housing 으로 채움). 자격 판정이 아니라 '어떤 집과 관련된 정책인지' 안내용
    housing_types: list[str] = Field(default_factory=list)       # 월세 · 전세 · 보증금 · 공공임대 · 기숙사
    housing_evidence: list[str] = Field(default_factory=list)    # 분류 근거 (예: "이름: '월세'")


class PolicyResult(BaseModel):
    available: bool
    query: str
    kind: str = "info"                    # info · eligibility · compare · apply · contract_safety
    items: list[PolicyItem] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    missing_names: list[str] = Field(default_factory=list)   # compare: 데이터에서 찾지 못한 정책 이름
    notes: list[str] = Field(default_factory=list)
    checklist: list[dict] = Field(default_factory=list)       # contract_safety
    checklist_source: str | None = None
    total_matched: int = 0
    reason: str | None = None
    source: str = SOURCE
    fetched_at: str | None = None
    official_links: list[dict] = Field(default_factory=lambda: list(OFFICIAL_LINKS))
    disclaimer: str = DISCLAIMER


def _curated_path() -> Path:
    return REPO_ROOT / "DB" / "reference" / "policies_curated.json"


def _path() -> Path | None:
    override = (_read("POLICIES_PATH") or "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else (REPO_ROOT / "BE" / p).resolve()
    # 수집 보고서(policies_<ts>.report.json)는 정책 목록이 아니다
    files = sorted(f for f in (REPO_ROOT / "DB" / "staging").glob("policies_*.json") if not f.name.endswith(".report.json"))
    if files:
        return files[-1]
    # 배포: 커밋해 둔 온통청년 수집 결과 (DB/staging 은 커밋하지 않음). 공공 API 데이터
    published = sorted(f for f in (REPO_ROOT / "DB" / "reference").glob("policies_youthcenter_*.json")
                       if not f.name.endswith(".report.json"))
    if published:
        return published[-1]
    # 온통청년 API 데이터가 없을 때만 임시 목록 (공식 페이지를 직접 확인해 옮겨 적은 것)
    return _curated_path() if _curated_path().exists() else None


def policy_source() -> str:
    p = _path()
    return CURATED_SOURCE if p is not None and p == _curated_path() else SOURCE


@lru_cache
def load_policies() -> tuple[PolicyItem, ...]:
    p = _path()
    if p is None or not p.exists():
        return ()
    data = json.loads(p.read_text(encoding="utf-8"))
    return tuple(classify_housing(PolicyItem.model_validate(x)) for x in data.get("items", []))


# ---------------------------------------------------------------- 주거 형태 분류 (규칙, LLM 없음)
# 원본 필드의 낱말로만 분류하고 근거를 남긴다. 걸리지 않으면 빈 목록(= 일반 주거 지원). 자격 판정에 쓰지 않는다.
# 온통청년 API 데이터를 받은 뒤 pipeline.policy_report 로 결과를 보고 낱말만 고친다.
HOUSING_FIELD_LABELS = {"name": "이름", "keywords": "키워드", "subcategory": "중분류", "support": "지원 내용"}
# 유형 → (낱말, 볼 필드). summary(정책 설명)는 다른 주거 형태를 함께 언급하는 일이 많아 보지 않는다.
# 보증금은 지원 내용에 '임차보증금은 제외' 같은 문장이 흔해 이름·키워드만 본다. 중분류는 기숙사만 본다
# (중분류 '전월세 및 주거급여 지원' 은 형태를 가리지 않는 일반 분류라서).
HOUSING_TYPE_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "공공임대": (("전세임대", "매입임대", "공공임대", "임대주택", "행복주택", "청년주택"), ("name", "keywords", "subcategory")),
    "기숙사": (("기숙사",), ("name", "keywords", "subcategory")),
    "월세": (("월세", "임차료", "월 임대료"), ("name", "keywords", "support")),
    "전세": (("전세",), ("name", "keywords", "support")),
    "보증금": (("보증금", "반환보증", "보증보험"), ("name", "keywords")),
}
# 형태를 가리지 않는 표현은 지우고 본다 ('전세임대'가 '전세'로 다시 잡히지 않게 공공임대 낱말도 지운다.
# 겹치는 낱말은 긴 복합어('전세임대')를 '임대주택'보다 먼저 지워야 해서 위 목록 순서가 중요하다)
_GENERIC_WORDS = ("전월세", "전·월세", *HOUSING_TYPE_RULES["공공임대"][0])


def _field_text(it: PolicyItem, field: str) -> str:
    v = getattr(it, field)
    return " ".join(v) if isinstance(v, list) else (v or "")


def classify_housing(it: PolicyItem) -> PolicyItem:
    """정책의 주거 형태 분류 + 근거. 이미 값이 있으면(수작업 보정) 그대로 둔다."""
    if it.housing_types:
        return it
    types: list[str] = []
    evidence: list[str] = []
    for t, (words, fields) in HOUSING_TYPE_RULES.items():
        for field in fields:
            text = _field_text(it, field)
            if t not in ("공공임대", "기숙사"):
                for g in _GENERIC_WORDS:
                    text = text.replace(g, " ")
            hit = next((w for w in words if w in text), None)
            if hit:
                types.append(t)
                evidence.append(f"{HOUSING_FIELD_LABELS[field]}: '{hit}'")
                break
    return it.model_copy(update={"housing_types": types, "housing_evidence": evidence})


_SPLIT = re.compile(r"[\s,·/]+")
# 사용자 표현 → 원본에 쓰이는 낱말 (검색만 넓힌다. 판정에는 쓰지 않음)
SYNONYMS = {"월세": ["월세", "임차", "주거비"], "전세": ["전세", "보증금", "임차"], "대출": ["대출", "융자", "이자"],
            "기숙사": ["기숙사", "연합기숙사"], "공공임대": ["공공임대", "임대주택", "행복주택", "매입임대"],
            "이사": ["이사", "중개", "중개보수"]}


NO_DATA = "정책 데이터를 아직 받지 않았습니다 (온통청년 OPEN API 키 발급 대기). 공식 사이트에서 확인해 주세요."


def _words(query: str) -> list[str]:
    words: list[str] = []
    for w in _SPLIT.split(query or ""):
        w = w.strip()
        if len(w) >= 2:
            words += SYNONYMS.get(w, [w])
    return list(dict.fromkeys(words))


def _score(it: PolicyItem, words: list[str], primary: list[str] | None = None) -> int:
    """이름에 맞는 정책을 앞에 (2026-11-16: 설명문이 긴 정책이 낱말 반복으로 앞서던 문제).
    낱말마다 '있는지'만 본다: 이름 5 · 키워드·중분류 2 · 설명·지원 내용 1. 사용자가 쓴 낱말(primary)은 동의어보다 2배.
    낱말을 붙인 말(예: '청년월세')이 이름에 그대로 있으면 +10."""
    name = it.name.replace(" ", "")
    head = f"{' '.join(it.keywords)} {it.subcategory or ''}"
    body = f"{it.summary or ''} {it.support or ''}"
    primary = primary or []
    score = 0
    for w in words:
        s = 5 * (w in name) + 2 * (w in head) + (w in body)
        score += s * (2 if w in primary else 1)
    joined = "".join(primary)
    if len(primary) >= 2 and joined in name:
        score += 10
    return score


_REGION_SUFFIX = re.compile(r"(특별자치시|특별자치도|특별시|광역시)$")
# 서울 자치구 25곳: '서대문구'만 말해도 서울시 단위 정책을 함께 찾는다
SEOUL_GU = ("종로구", "중구", "용산구", "성동구", "광진구", "동대문구", "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구",
            "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구", "영등포구", "동작구", "관악구", "서초구", "강남구",
            "송파구", "강동구")


def expand_regions(regions: list[str]) -> list[str]:
    out = [r.strip() for r in regions if r and r.strip()]
    if any(r in SEOUL_GU for r in out) and not any("서울" in r for r in out):
        out.insert(0, "서울")
    return list(dict.fromkeys(out))


def region_match(item_region: str | None, regions: list[str]) -> bool:
    """지역 조건: 원본 지역이 없거나 '전국'이면 통과, 아니면 말한 지역(시·구) 중 하나와 겹칠 때.
    '서울특별시' ↔ '서울' 처럼 접미사 차이는 맞춘다."""
    if not regions or not item_region or "전국" in item_region:
        return True
    r = _REGION_SUFFIX.sub("", item_region)
    return any(_REGION_SUFFIX.sub("", t) in r or r in _REGION_SUFFIX.sub("", t) for t in regions if t.strip())


def search_policies(query: str = "", region: str | None = None, limit: int = 5,
                    items: tuple[PolicyItem, ...] | None = None, regions: list[str] | None = None,
                    kind: str = "info") -> PolicyResult:
    """주거 정책 검색 (낱말 일치 점수, 이름·키워드 가중). 자격 조건으로 거르지 않는다 (지역 표기로만 거른다)."""
    regions = expand_regions([*(regions or []), *([region] if region else [])])
    notes = [KIND_NOTES[kind]] if kind in KIND_NOTES else []
    if regions:
        notes.append(KIND_NOTES["region"])
    items = load_policies() if items is None else items
    if not items:
        return PolicyResult(available=False, query=query, kind=kind, regions=regions, notes=notes, reason=NO_DATA)
    words = _words(query)
    primary = [w for w in (x.strip() for x in _SPLIT.split(query or "")) if len(w) >= 2]
    pool = [it for it in items if region_match(it.region, regions)]
    ranked = sorted(pool, key=lambda it: (-_score(it, words, primary), it.name)) if words else sorted(pool, key=lambda it: it.name)
    if words:
        ranked = [it for it in ranked if _score(it, words, primary) > 0] or ranked
    seen: set[str] = set()  # 같은 이름(기관만 다른 중복 등록)은 하나만
    ranked = [it for it in ranked if not (it.name.replace(" ", "") in seen or seen.add(it.name.replace(" ", "")))]
    fetched = max((it.fetched_at for it in items if it.fetched_at), default=None)
    return PolicyResult(available=True, query=query, kind=kind, items=ranked[:limit], total_matched=len(ranked),
                        regions=regions, notes=notes, fetched_at=fetched, source=policy_source())


def compare_policies(names: list[str], items: tuple[PolicyItem, ...] | None = None) -> PolicyResult:
    """정책 이름마다 가장 잘 맞는 정책 하나씩 나란히 (유불리 판단 없음). 못 찾은 이름은 missing_names 로."""
    names = [n.strip() for n in names if n and n.strip()]
    query = " · ".join(names)
    notes = [KIND_NOTES["compare"]]
    items = load_policies() if items is None else items
    if not items:
        return PolicyResult(available=False, query=query, kind="compare", notes=notes, missing_names=names, reason=NO_DATA)
    picked: list[PolicyItem] = []
    missing: list[str] = []
    for n in names:
        words = _words(n)
        best = max(items, key=lambda it: (_score(it, words), -len(it.name)), default=None)
        if best is None or _score(best, words) <= 0:
            missing.append(n)
        elif best not in picked:
            picked.append(best)
    if missing:
        notes.append(f"공식 데이터에서 찾지 못한 정책: {', '.join(missing)}. 공식 사이트에서 확인해 주세요.")
    fetched = max((it.fetched_at for it in items if it.fetched_at), default=None)
    return PolicyResult(available=True, query=query, kind="compare", items=picked, total_matched=len(picked),
                        missing_names=missing, notes=notes, fetched_at=fetched, source=policy_source())


def contract_checklist() -> PolicyResult:
    """계약 안전 점검 목록 (정책 데이터와 상관없이 늘 안내). 판정·보장하지 않는다."""
    links = list(dict.fromkeys((i["link"]["name"], i["link"]["url"]) for s in CONTRACT_CHECKLIST for i in s["items"] if i["link"]))
    return PolicyResult(
        available=True, query="계약 안전 점검", kind="contract_safety", checklist=CONTRACT_CHECKLIST,
        checklist_source=CHECKLIST_SOURCE, source="공식 사이트 안내 기반 일반 점검 목록",
        official_links=[{"name": n, "url": u} for n, u in links],
        notes=["이 목록을 모두 확인해도 피해를 완전히 막는다고 보장할 수 없어요. 계약서 내용은 공인중개사·법률 상담으로 확인해 주세요."],
        disclaimer=CHECKLIST_SOURCE,
    )


# ---------------------------------------------------------------- 매물별 계약 전 확인 (2026-11-16)
# 일반 점검 목록의 항목 옆에, 서비스가 이 매물에 대해 이미 가진 사실(실거래 비교·건축물대장·보증금)을 붙인다.
# 판정하지 않는다: "안전합니다/위험합니다/조건 충족" 같은 결론을 내지 않고 확인할 근거만 보여 준다.

CONTRACT_CHECK_NOTE = ("서비스가 가진 데이터로 각 항목 옆에 이 매물의 사실을 붙였어요. 안전 여부를 판정하지 않으며, "
                       "광고 정보·과거 실거래·공공 대장 기준이라 실제 계약 내용과 다를 수 있어요.")


class ContractCheckItem(BaseModel):
    key: str | None = None
    text: str
    link: dict | None = None
    fact: str | None = None               # 이 매물에서 서비스가 확인한 사실 (판정 아님). None = 붙일 데이터 없음


class ContractCheckPhase(BaseModel):
    phase: str
    items: list[ContractCheckItem]


class ContractCheck(BaseModel):
    property_id: str
    phases: list[ContractCheckPhase]
    source: str = CHECKLIST_SOURCE
    notes: list[str] = Field(default_factory=list)


def _n(x: float | None) -> str:
    return "정보 없음" if x is None else f"{x:g}"


def _market_fact(m) -> str:
    if not m.available:
        return f"이 매물은 실거래 비교를 하지 못했어요: {m.reason or '비교할 거래 없음'}"
    scope = "같은 동" if m.scope == "동" else "같은 구"
    s = (f"이 매물 월세 {_n(m.listing_monthly_rent_manwon)}만 원 · {scope} 비슷한 면적 월세 실거래 {m.n}건의 "
         f"월세 중앙값 {_n(m.rent_median)}만 원, 보증금 중앙값 {_n(m.deposit_median)}만 원")
    return s + (f" (서비스 비교: {m.level})" if m.level else " (표본이 적어 높고 낮음은 판단하지 않음)")


def _purpose(b) -> str:
    return " / ".join(x for x in (b.main_purpose, b.detail_purpose) if x)


def _building_fact(b) -> str:
    if not b.available or not _purpose(b):
        return f"이 매물의 건축물대장을 찾지 못했어요{': ' + b.reason if b.reason else ''}. 세움터에서 직접 확인해 주세요."
    # 표제부는 건물 전체 기준이라 계약할 호실의 용도는 알 수 없다 (호실 용도 = 전유부)
    s = f"건축물대장(건물 전체 기준) 용도: {_purpose(b)}"
    if b.use_approval_date:
        s += f" · 사용승인 {b.use_approval_date}"
    if "근린생활" in (b.main_purpose or ""):
        s += " — 건물 주용도가 근린생활시설이에요. 위 설명처럼 전입신고·보증보험 가입 가능 여부를 꼭 확인해 보세요"
    elif "근린생활" in (b.detail_purpose or ""):
        s += " — 같은 건물에 근린생활시설이 함께 등록돼 있어요. 계약할 호실의 용도는 건축물대장 전유부(호실별)에서 확인해 주세요"
    s += ". 위반건축물 표시는 대장 원본에서 확인해 주세요."
    return s


def _multi_family_fact(b) -> str | None:
    if b.available and "다가구" in _purpose(b):
        return "건축물대장 용도가 다가구주택으로 등록돼 있어요. 이 항목을 확인해 보세요."
    return None


def contract_check_for(p, market=None, building=None) -> ContractCheck:
    """매물별 계약 전 확인 목록. market·building 을 주지 않으면 서비스 데이터(로컬 캐시)로 계산한다."""
    if market is None:
        from app.market import compare_property
        market = compare_property(p)
    if building is None:
        from app.building_register import building_for
        building = building_for(p)
    facts = {
        "market": _market_fact(market),
        "building": _building_fact(building),
        "multi_family": _multi_family_fact(building),
        "deposit": f"이 매물 보증금 {_n(p.deposit_manwon)}만 원 (광고 기준)" if p.deposit_manwon is not None else None,
    }
    phases = [
        ContractCheckPhase(phase=s["phase"], items=[
            ContractCheckItem(key=i.get("key"), text=i["text"], link=i["link"], fact=facts.get(i.get("key") or ""))
            for i in s["items"]])
        for s in CONTRACT_CHECKLIST
    ]
    return ContractCheck(property_id=p.property_id, phases=phases, notes=[CONTRACT_CHECK_NOTE])


# ---------------------------------------------------------------- 매물과 관련된 정책 안내 (2026-11-16)
# 매물의 임대 형태(월세/전세)와 주소의 시·구로 '이런 집과 관련된 공식 정책'을 보여 준다.
# 대상자 여부를 판정하지 않고, 지원 금액을 유지비·점수에 넣지 않는다 (CLAUDE.md 0-7).

RELATED_NOTE = ("이 매물의 임대 형태·지역과 관련된 공식 정책을 안내합니다. 받을 수 있는지(자격)는 판단하지 않으며, "
                "지원 금액은 유지비·추천 점수에 넣지 않습니다.")
# 임대 형태 → 관련 정책 유형 (공공임대·기숙사는 이 매물이 아닌 다른 선택지라 매물 카드에는 넣지 않는다)
RELATED_TYPES = {"월세": ("월세", "보증금"), "전세": ("전세", "보증금")}


class RelatedPolicies(BaseModel):
    property_id: str
    rent_kind: str | None                 # 월세 · 전세 · None(알 수 없음)
    regions: list[str] = Field(default_factory=list)
    available: bool
    items: list[PolicyItem] = Field(default_factory=list)
    reason: str | None = None
    source: str = SOURCE
    fetched_at: str | None = None
    notes: list[str] = Field(default_factory=list)
    official_links: list[dict] = Field(default_factory=lambda: list(OFFICIAL_LINKS))
    disclaimer: str = DISCLAIMER


def rent_kind(p) -> str | None:
    """광고의 월세·보증금으로 본 임대 형태. 월세 > 0 이면 월세, 월세 0·보증금 > 0 이면 전세."""
    if p.monthly_rent_manwon is not None and p.monthly_rent_manwon > 0:
        return "월세"
    if p.monthly_rent_manwon == 0 and (p.deposit_manwon or 0) > 0:
        return "전세"
    return None


def address_regions(address: str | None) -> list[str]:
    """'서울특별시 서대문구 대현동 …' → ['서울특별시', '서대문구'] (시·도와 시·군·구까지만)."""
    parts = (address or "").split()
    out: list[str] = []
    for w in parts[:3]:
        if re.search(r"(특별시|광역시|특별자치시|특별자치도|도)$", w) and not out:
            out.append(w)
        elif re.search(r"[시군구]$", w):
            out.append(w)
            break
    return out


def related_policies_for(p, items: tuple[PolicyItem, ...] | None = None, limit: int = 6) -> RelatedPolicies:
    kind = rent_kind(p)
    regions = expand_regions(address_regions(p.address))
    items = load_policies() if items is None else items
    base = dict(property_id=p.property_id, rent_kind=kind, regions=regions, notes=[RELATED_NOTE],
                source=policy_source())
    if not items:
        return RelatedPolicies(**base, available=False, reason=NO_DATA)
    if kind is None:
        return RelatedPolicies(**base, available=False, reason="광고에 월세·보증금 정보가 없어 임대 형태를 알 수 없습니다.")
    want = RELATED_TYPES[kind]
    picked = [it for it in items if region_match(it.region, regions) and any(t in want for t in it.housing_types)]
    # 이 형태를 직접 가리키는 정책(월세/전세)을 먼저, 그다음 보증금 관련. 같은 순위면 지역 정책을 먼저
    picked.sort(key=lambda it: (kind not in it.housing_types, not it.region or "전국" in (it.region or ""), it.name))
    seen: set[str] = set()  # 같은 이름의 정책(기관만 다른 중복 등록)은 하나만
    picked = [it for it in picked if not (it.name.replace(" ", "") in seen or seen.add(it.name.replace(" ", "")))]
    fetched = max((it.fetched_at for it in items if it.fetched_at), default=None)
    return RelatedPolicies(**base, available=True, items=picked[:limit], fetched_at=fetched)
