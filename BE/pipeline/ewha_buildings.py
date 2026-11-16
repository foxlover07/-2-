"""이화여대 캠퍼스 건물 참조 테이블 생성 (DB/ref/ewha_buildings.csv).

사용 (BE/ 에서):
    python -m pipeline.ewha_buildings

- 건물 목록(BUILDINGS)은 Kakao 키워드 검색(캠퍼스 rect)으로 '학교부속시설' 등을 모은 뒤
  강의·학습 건물만 고른 것이다 (기숙사·부속학교·ATM·편의점·경비실·같은 건물 내 연구소 제외, 2026-11-06).
- 좌표는 Kakao POI 대표 지점이며 출입구가 아니다 (entrance_verified=false).
- place_name 이 정확히 1건 일치할 때만 채택한다. 아니면 멈춘다.
- 출입구 후보·캠퍼스 문(EXTRA_POINTS)은 별도 행으로 둔다.
"""

from __future__ import annotations

import csv
from datetime import date

from app.config import REPO_ROOT, get_settings
from app.tools.kakao import KakaoClient

OUT = REPO_ROOT / "DB" / "ref" / "ewha_buildings.csv"
CAMPUS_RECT = "126.9400,37.5570,126.9530,37.5700"

# (destination_id, poc_ewha.csv spotN_name 대응, building_type, Kakao place_name)
BUILDINGS: list[tuple[str, str, str, str]] = [
    ("EWHA_ECC", "ECC", "lecture", "이화여자대학교 ECC 영산극장"),
    ("EWHA_POSCO", "이화·포스코관", "lecture", "이화여자대학교 이화포스코관"),
    ("EWHA_HAKGWAN", "학관", "lecture", "이화여자대학교 학관"),
    ("EWHA_ENG", "공과대학", "lecture", "이화여자대학교 엘텍공과대학"),
    ("EWHA_ASAN_ENG", "", "lecture", "이화여자대학교 아산공학관"),
    ("EWHA_INMUN", "", "lecture", "이화여자대학교 인문관"),
    ("EWHA_EDU", "", "lecture", "이화여자대학교 교육관"),
    ("EWHA_SCI_A", "", "lecture", "이화여자대학교 종합과학관A동"),
    ("EWHA_SCI_B", "", "lecture", "이화여자대학교 종합과학관B동"),
    ("EWHA_SCI_C", "", "lecture", "이화여자대학교 종합과학관C동"),
    ("EWHA_SCI_D", "", "lecture", "이화여자대학교 종합과학관D동"),
    ("EWHA_JINSEONMI", "", "lecture", "이화여자대학교 진선미관"),
    ("EWHA_MAIN_HALL", "", "admin", "이화여자대학교 본관"),
    ("EWHA_PHARM_A", "", "lecture", "이화여자대학교 약학관A동"),
    ("EWHA_PHARM_B", "", "lecture", "이화여자대학교 약학관B동"),
    ("EWHA_STUDENT_CULTURE", "", "student", "이화여자대학교 학생문화관"),
    ("EWHA_HUMAN_ECOLOGY", "", "lecture", "이화여자대학교 생활환경관"),
    ("EWHA_GRAD", "", "lecture", "이화여자대학교 대학원관"),
    ("EWHA_AUDITORIUM", "", "event", "이화여자대학교 대강당"),
    ("EWHA_HELEN", "", "lecture", "이화여자대학교 헬렌관"),
    ("EWHA_ARYEONGDANG", "", "lecture", "이화여자대학교 아령당"),
    ("EWHA_LAW", "", "lecture", "이화여자대학교 법학관"),
    ("EWHA_LIBRARY", "", "library", "이화여자대학교 중앙도서관"),
    ("EWHA_MUSIC", "", "lecture", "이화여자대학교 음악관"),
    ("EWHA_ART_A", "", "lecture", "이화여자대학교 조형예술관 A동"),
    ("EWHA_ART_B", "", "lecture", "이화여자대학교 조형예술관 B동"),
    ("EWHA_ART_C", "", "lecture", "이화여자대학교 조형예술관C동"),
    ("EWHA_GYM_A", "", "sports", "이화여자대학교 체육관A동"),
    ("EWHA_GYM_B", "", "sports", "이화여자대학교 체육관B동"),
    ("EWHA_GYM_C", "", "sports", "이화여자대학교 체육관C동"),
    ("EWHA_SHINSEGAE", "", "lecture", "이화신세계관"),
    ("EWHA_SKT", "", "lecture", "이화여자대학교 SK텔레콤관"),
    ("EWHA_SAMSUNG_EDU", "", "lecture", "이화여자대학교 삼성교육문화관"),
    ("EWHA_INTL_EDU", "", "lecture", "이화여자대학교 국제교육관"),
    ("EWHA_RESEARCH_COOP", "", "research", "이화여자대학교 연구협력관"),
    ("EWHA_INDUSTRY_COOP", "", "research", "이화여자대학교 산학협력관"),
]

NOTES = {
    "EWHA_ECC": "카카오 POI 대표 지점. 출입구 아님. 카카오 표기는 '영산극장'",
    "EWHA_ENG": "카카오 POI 대표 지점. 신공학관 POI와 거의 같은 위치. 수동 측정 '공과대학'은 아산공학관과 더 잘 맞음(2026-11-06 비교)",
}

# (destination_id, csv_spot_name, point_role, Kakao place_name, note)
EXTRA_POINTS: list[tuple[str, str, str, str, str]] = [
    ("EWHA_HAKGWAN_ENT", "학관", "entrance_candidate", "이화여자대학교 학관 출입구", "카카오 '입출구' 분류 POI. 어느 출입구인지 현장 확인 필요"),
    ("EWHA_ENG_GATE", "공과대학", "entrance_candidate", "이대공대 철문", "카카오 '입출구' 분류 POI. 건물 출입구가 아니라 캠퍼스 문일 수 있음"),
    ("EWHA_MAIN_GATE", "", "campus_gate", "이화여자대학교 정문", "캠퍼스 정문"),
    ("EWHA_BACK_GATE", "", "campus_gate", "이화여자대학교 후문", "캠퍼스 후문"),
]

FIELDS = ["destination_id", "university", "csv_spot_name", "point_role", "building_type", "kakao_place_name",
          "kakao_place_id", "latitude", "longitude", "coord_source", "checked_at", "entrance_verified", "note"]


def _find(client: KakaoClient, name: str) -> dict:
    docs = [d for d in client.search_keyword(name, size=15, rect=CAMPUS_RECT)["documents"] if d["place_name"] == name]
    if len(docs) != 1:
        raise SystemExit(f"'{name}' 정확 일치 {len(docs)}건 — 목록을 확인한다")
    return docs[0]


def main() -> None:
    today = date.today().isoformat()
    client = KakaoClient(get_settings().kakao_rest_api_key)
    rows = []
    try:
        for did, spot, btype, name in BUILDINGS:
            d = _find(client, name)
            rows.append({"destination_id": did, "university": "이화여자대학교", "csv_spot_name": spot,
                         "point_role": "building_poi", "building_type": btype, "kakao_place_name": name,
                         "kakao_place_id": d["id"], "latitude": d["y"], "longitude": d["x"],
                         "coord_source": "kakao_keyword_search", "checked_at": today, "entrance_verified": "false",
                         "note": NOTES.get(did, "카카오 POI 대표 지점. 출입구 아님")})
        for did, spot, role, name, note in EXTRA_POINTS:
            d = _find(client, name)
            rows.append({"destination_id": did, "university": "이화여자대학교", "csv_spot_name": spot,
                         "point_role": role, "building_type": "", "kakao_place_name": name,
                         "kakao_place_id": d["id"], "latitude": d["y"], "longitude": d["x"],
                         "coord_source": "kakao_keyword_search", "checked_at": today, "entrance_verified": "false",
                         "note": note})
    finally:
        client.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(OUT, len(rows))


if __name__ == "__main__":
    main()
