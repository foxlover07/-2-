"""파이프라인이 다루는 대학 (--univ 키 → 대학 이름·건물 참조 테이블).

키를 주지 않으면 기존처럼 이화여대(ewha)다.
"""

from __future__ import annotations

from app.config import REPO_ROOT

# id_prefix: 추천 후보 CSV 의 property_id 앞에 붙인다. 이대·서강대처럼 같은 매물이 두 대학 후보에 들어가도
# ID 가 겹치지 않게 한다. 이대는 기존 ID(zigbang:…)를 그대로 둔다.
UNIVERSITIES: dict[str, dict[str, str]] = {
    "ewha": {"name": "이화여자대학교", "ref": "ewha_buildings.csv", "id_prefix": ""},
    "korea": {"name": "고려대학교", "ref": "korea_buildings.csv", "id_prefix": "ku"},
    "sogang": {"name": "서강대학교", "ref": "sogang_buildings.csv", "id_prefix": "sg"},
    "seoultech": {"name": "서울과학기술대학교", "ref": "seoultech_buildings.csv", "id_prefix": "st"},
}
DEFAULT = "ewha"


def candidate_id(key: str, property_id: str) -> str:
    prefix = get(key)["id_prefix"]
    return f"{prefix}:{property_id}" if prefix else property_id


def get(key: str = DEFAULT) -> dict[str, str]:
    if key not in UNIVERSITIES:
        raise SystemExit(f"모르는 대학 키: {key!r} (가능: {', '.join(UNIVERSITIES)})")
    return UNIVERSITIES[key]


def ref_path(key: str = DEFAULT):
    return REPO_ROOT / "DB" / "ref" / get(key)["ref"]
