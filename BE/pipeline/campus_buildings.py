"""캠퍼스 건물 참조 테이블 생성 — 이대 외 대학 (DB/ref/<대학>_buildings.csv).

사용 (BE/ 에서):
    python -m pipeline.campus_buildings korea sogang seoultech

- 이대는 기존 pipeline/ewha_buildings.py 를 그대로 쓴다 (같은 열·같은 규칙).
- Kakao 키워드 검색(캠퍼스 rect, 서강대처럼 rect 에 잘 안 잡히면 rect 없이)에서
  place_name 이 정확히 1건 일치할 때만 채택한다. 아니면 멈춘다.
- 좌표는 Kakao POI 대표 지점이며 출입구가 아니다 (entrance_verified=false).
- 기본 강의동 4곳(spot 이름이 있는 행)은 2026-11-09 임시 선정 (캠퍼스 안에서 고르게). 팀 확인 필요.
"""

from __future__ import annotations

import csv
import sys
from datetime import date

from app.config import get_settings
from app.tools.kakao import KakaoClient
from pipeline import universities
from pipeline.ewha_buildings import FIELDS

DEFAULT_NOTE = "카카오 POI 대표 지점. 출입구 아님"

# 대학 키 → rect, 건물 [(destination_id, 기본 강의동 이름, building_type, Kakao place_name, note)], 기타 지점
SPECS: dict[str, dict] = {
    "korea": {
        "rect": "127.0200,37.5810,127.0400,37.5950",
        "buildings": [
            ("KU_POLSCI", "정경관", "lecture", "고려대학교 상남정경관",
             "카카오 POI 대표 지점. 출입구 아님. 카카오 표기 '상남정경관'(정치외교학과·행정학과 POI 같은 위치). 이름 팀 확인 필요"),
            ("KU_LGPOSCO", "LG포스코경영관", "lecture", "고려대학교 LG포스코경영관", ""),
            ("KU_LIBARTS_W", "문과대학 서관", "lecture", "고려대학교 문과대학서관", ""),
            ("KU_NEW_ENG", "신공학관", "lecture", "고려대학교 신공학관",
             "카카오 POI 대표 지점. '서울캠퍼스 신공학관' POI 와 거의 같은 위치"),
            ("KU_WOOJUNG", "", "lecture", "고려대학교 우정정보관", ""),
            ("KU_ASAN_SCI", "", "lecture", "고려대학교 아산이학관", ""),
            ("KU_HANA_SCI", "", "lecture", "고려대학교 하나과학관", ""),
            ("KU_CJ_LAW", "", "lecture", "고려대학교 CJ법학관", ""),
            ("KU_INTL", "", "lecture", "고려대학교 국제관", ""),
            ("KU_MEDIA", "", "lecture", "고려대학교 미디어관", ""),
            ("KU_EDU_MAIN", "", "lecture", "고려대학교 사범대학 본관", ""),
            ("KU_HYUNDAI_BIZ", "", "lecture", "고려대학교 현대자동차경영관", ""),
            ("KU_BIZ_MAIN", "", "lecture", "고려대학교 경영본관", ""),
            ("KU_ENG", "", "lecture", "고려대학교 공학관", ""),
            ("KU_CHANGUI", "", "lecture", "고려대학교 창의관", ""),
            ("KU_LIFE_SCI_E", "", "lecture", "고려대학교 생명과학관동관", ""),
            ("KU_MEDIHEAL", "", "lecture", "고려대학교 메디힐지구환경관", ""),
            ("KU_SK_FUTURE", "", "lecture", "고려대학교 서울캠퍼스 SK미래관", ""),
            ("KU_UNCHO_EDU", "", "lecture", "고려대학교 운초우선교육관", ""),
            ("KU_LAW_OLD", "", "lecture", "고려대학교 법학관구관", ""),
            ("KU_LAW_NEW", "", "lecture", "고려대학교서울캠퍼스 법학관신관", ""),
            ("KU_MED_MAIN", "", "lecture", "고려대학교 의과대학본관", ""),
            ("KU_KOREAN_EDU", "", "lecture", "고려대학교 한국어교육관", ""),
            ("KU_MAIN_HALL", "", "admin", "고려대학교 본관", ""),
            ("KU_LIBRARY", "", "library", "고려대학교 서울캠퍼스 중앙도서관", ""),
            ("KU_SCI_LIBRARY", "", "library", "고려대학교 과학도서관", ""),
            ("KU_CENTENNIAL", "", "library", "고려대학교 백주년기념삼성관", ""),
            ("KU_HANA_SQUARE", "", "student", "고려대학교 하나스퀘어", ""),
        ],
        "extra": [
            ("KU_POLSCI_BACK", "정경관", "entrance_candidate", "고려대학교 정경관후문", "카카오 '입출구' 분류 POI. 정경관 쪽 문, 현장 확인 필요"),
            ("KU_MAIN_GATE", "", "campus_gate", "고려대학교 서울캠퍼스 정문", "캠퍼스 정문"),
        ],
    },
    "sogang": {
        "rect": None,  # 캠퍼스 rect 검색에 주요 강의동 상당수가 안 잡혀 이름 검색으로 찾는다
        "buildings": [
            ("SG_JUNGHASANG", "정하상관", "lecture", "서강대학교 정하상관", "카카오 POI 대표 지점. 출입구 아님. 학내 약칭 J관"),
            ("SG_KIMDAEGEON", "김대건관", "lecture", "서강대학교 김대건관", "카카오 POI 대표 지점. 출입구 아님. 학내 약칭 K관"),
            ("SG_RICCI", "리치과학관", "lecture", "서강대학교 리치과학관", "카카오 POI 대표 지점. 출입구 아님. 학내 약칭 R관"),
            ("SG_PAUL_BIZ", "바오로경영관", "lecture", "서강대학교 금호아시아나바오로경영관",
             "카카오 POI 대표 지점. 출입구 아님. 카카오 표기 '금호아시아나바오로경영관'"),
            ("SG_DASAN", "", "lecture", "서강대학교 다산관", ""),
            ("SG_GABRIEL", "", "lecture", "서강대학교 삼성가브리엘관", ""),
            ("SG_MATTEO", "", "lecture", "서강대학교 마태오관", ""),
            ("SG_EMMAUS", "", "lecture", "서강대학교 엠마오관", ""),
            ("SG_THOMAS_MORE", "", "lecture", "서강대학교 토마스모어관", ""),
            ("SG_XAVIER", "", "lecture", "서강대학교 하비에르관", ""),
            ("SG_ADAM_SCHALL", "", "lecture", "서강대학교 아담샬관", ""),
            ("SG_TEILHARD", "", "lecture", "서강대학교 떼이야르관", ""),
            ("SG_RICCI_ANNEX", "", "lecture", "서강대학교 리치별관", ""),
            ("SG_BERCHMANS", "", "student", "서강대학교 베르크만스 우정원", ""),
            ("SG_MAIN_HALL", "", "admin", "서강대학교 본관", ""),
            ("SG_LIBRARY", "", "library", "서강대학교 로욜라도서관", ""),
            ("SG_MARY_HALL", "", "event", "서강대학교 메리홀", ""),
            ("SG_GYM", "", "sports", "서강대학교 체육관", ""),
        ],
        "extra": [
            ("SG_MAIN_GATE", "", "campus_gate", "서강대학교 정문", "캠퍼스 정문"),
            ("SG_BACK_GATE", "", "campus_gate", "서강대학교 후문", "캠퍼스 후문"),
        ],
    },
    "seoultech": {
        "rect": "127.0700,37.6260,127.0880,37.6390",
        "buildings": [
            ("ST_CHANGHAK", "창학관", "lecture", "서울과학기술대학교 창학관", ""),
            ("ST_FRONTIER", "프론티어관", "lecture", "서울과학기술대학교 프론티어관", ""),
            ("ST_MIRAE", "미래관", "lecture", "서울과학기술대학교 미래관", ""),
            ("ST_DAVINCI", "다빈치관", "lecture", "서울과학기술대학교 다빈치관", ""),
            ("ST_HITECH", "", "lecture", "서울과학기술대학교 하이테크관", ""),
            ("ST_100TH", "", "lecture", "서울과학기술대학교 100주년기념관", ""),
            ("ST_SANGSANG", "", "lecture", "서울과학기술대학교 상상관", ""),
            ("ST_TECHNOCUBE", "", "lecture", "서울과학기술대학교 테크노큐브", ""),
            ("ST_EOUI", "", "lecture", "서울과학기술대학교 어의관", ""),
            ("ST_CHEONGUN", "", "lecture", "서울과학기술대학교 청운관", ""),
            ("ST_HYESEONG", "", "lecture", "서울과학기술대학교 혜성관", ""),
            ("ST_DASAN", "", "lecture", "서울과학기술대학교 다산관", ""),
            ("ST_MUGUNG", "", "lecture", "서울과학기술대학교 무궁관", ""),
            ("ST_DAERYUK", "", "lecture", "서울과학기술대학교 대륙관", ""),
            ("ST_AREUM", "", "lecture", "서울과학기술대학교 아름관", ""),
            ("ST_POWER_PLANT", "", "lecture", "서울과학기술대학교 파워플랜트", ""),
            ("ST_LIBRARY", "", "library", "서울과학기술대학교 중앙도서관", ""),
            ("ST_STUDENT1", "", "student", "서울과학기술대학교 제1학생회관", ""),
        ],
        "extra": [
            ("ST_MAIN_GATE", "", "campus_gate", "서울과학기술대학교 정문", "캠퍼스 정문"),
        ],
    },
}


def _find(client: KakaoClient, name: str, rect: str | None) -> dict:
    kwargs = {"size": 15}
    if rect:
        kwargs["rect"] = rect
    docs = [d for d in client.search_keyword(name, **kwargs)["documents"] if d["place_name"] == name]
    if len(docs) != 1:
        raise SystemExit(f"'{name}' 정확 일치 {len(docs)}건 — 목록을 확인한다")
    return docs[0]


def build(univ: str, client: KakaoClient) -> int:
    spec, uni_name, today = SPECS[univ], universities.get(univ)["name"], date.today().isoformat()
    rows = []
    for did, spot, btype, name, note in spec["buildings"]:
        d = _find(client, name, spec["rect"])
        rows.append({"destination_id": did, "university": uni_name, "csv_spot_name": spot,
                     "point_role": "building_poi", "building_type": btype, "kakao_place_name": name,
                     "kakao_place_id": d["id"], "latitude": d["y"], "longitude": d["x"],
                     "coord_source": "kakao_keyword_search", "checked_at": today, "entrance_verified": "false",
                     "note": note or DEFAULT_NOTE})
    for did, spot, role, name, note in spec["extra"]:
        d = _find(client, name, spec["rect"])
        rows.append({"destination_id": did, "university": uni_name, "csv_spot_name": spot,
                     "point_role": role, "building_type": "", "kakao_place_name": name,
                     "kakao_place_id": d["id"], "latitude": d["y"], "longitude": d["x"],
                     "coord_source": "kakao_keyword_search", "checked_at": today, "entrance_verified": "false",
                     "note": note})
    out = universities.ref_path(univ)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(out, len(rows))
    return len(rows)


def main(keys: list[str]) -> None:
    client = KakaoClient(get_settings().kakao_rest_api_key)
    try:
        for k in keys:
            if k not in SPECS:
                raise SystemExit(f"SPECS 에 없는 대학: {k} (가능: {', '.join(SPECS)})")
            build(k, client)
    finally:
        client.close()


if __name__ == "__main__":
    main(sys.argv[1:] or list(SPECS))
