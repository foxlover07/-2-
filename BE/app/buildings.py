"""시간표 건물 이름 → 표준 이름 (도보시간 표의 building 값).

시간표를 직접 입력하거나 시간표 사진 인식(예정)에서 나온 건물 이름은 줄임말·띄어쓰기·가운뎃점이 제각각이다.
추천 계산 전에 표준 이름으로 바꾼다.

1) 표기 정규화: 공백·가운뎃점(·)·'이화여자대학교'/'이화여대' 접두어를 무시하고 비교 (예: '이화포스코관' = '이화·포스코관').
2) 줄임말 표(ALIASES): 정규화로 안 되는 이름. ⚠️ 초안, 팀 확인 필요 — 실제 학생들이 쓰는 줄임말인지 확인한 뒤 늘린다.
   여러 건물을 가리킬 수 있는 이름(예: '종과' → 종합과학관 A~D동)은 넣지 않는다. 틀리게 바꾸는 것보다 모르는 건물로 두는 게 낫다.
"""

from collections.abc import Iterable

# 줄임말·다른 이름 → 표준 이름. ⚠️ 초안, 팀 확인 필요.
ALIASES: dict[str, str] = {
    "포관": "이화·포스코관",
    "포스코관": "이화·포스코관",
    "공대": "공과대학",
    "엘텍공과대학": "공과대학",   # Kakao 장소 이름 (ref/ewha_buildings.csv)
    "엘텍": "공과대학",
    "ECC영산극장": "ECC",          # Kakao 장소 이름
    "이씨씨": "ECC",
    "생환관": "생활환경관",
    "조예관A동": "조형예술관 A동",
    "조형관A동": "조형예술관 A동",
    "약대A동": "약학관A동",
}

_PREFIXES = ("이화여자대학교", "이화여대")


def normalize(name: str) -> str:
    """비교용 키: 접두어·공백·가운뎃점 제거, 영문 대문자."""
    s = name.strip()
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
    return "".join(ch for ch in s if not ch.isspace() and ch not in "·・.").upper()


def resolve(name: str, known: Iterable[str]) -> str:
    """시간표 건물 이름 → 표준 이름. 표준 이름 목록(known)에서 찾지 못하면 입력 그대로 돌려준다 (호출한 쪽이 '모르는 건물'로 처리)."""
    by_key = {normalize(k): k for k in known}
    key = normalize(name)
    if key in by_key:
        return by_key[key]
    alias = {normalize(a): t for a, t in ALIASES.items()}.get(key)
    if alias is not None and normalize(alias) in by_key:
        return by_key[normalize(alias)]
    return name


def aliases_for(name: str) -> list[str]:
    """이 표준 이름을 가리키는 줄임말 (화면 안내용)."""
    return [a for a, t in ALIASES.items() if normalize(t) == normalize(name)]
