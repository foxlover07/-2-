"""테스트는 로컬 .env/.env.local 의 DATA_CSV_PATH 와 상관없이 팀 PoC 데이터(DB/poc_ewha.csv)로 돈다.

프로세스 환경 변수가 .env 파일보다 우선하므로(app.config._read) 여기서 기본 경로로 고정한다.
"""

import os

import pytest

from app.data import DEFAULT_CSV_PATH, load_all_properties, load_properties, load_walk_table
from app.listing_status import load_status

os.environ["DATA_CSV_PATH"] = str(DEFAULT_CSV_PATH)
os.environ["WALK_TABLE_PATH"] = ""  # 빈 값 = 도보시간 표 없음 (spot1~4 만)
os.environ["AGENT_TRACE_PATH"] = ""  # 테스트에서 에이전트 턴 기록 파일을 쓰지 않는다
os.environ["AGENT_LLM_EXPLAIN"] = "on"
os.environ["FREQUENT_PLACES_KAKAO"] = "off"  # 자주 가는 곳: 실제 카카오를 부르지 않는다 (테스트는 가짜 클라이언트)
os.environ["FREQUENT_WALK_CACHE_PATH"] = ""  # 메모리만
os.environ["FREQUENT_CALL_LOG_PATH"] = ""
os.environ["BUILDING_WALK_KAKAO"] = "off"  # 고른 건물 도보 계산: 실제 카카오를 부르지 않는다
os.environ["BUILDING_WALK_CACHE_PATH"] = ""
os.environ["BUILDING_CALL_LOG_PATH"] = ""
os.environ["BUILDING_LOOKUP_KAKAO"] = "off"  # 목록 밖 건물 카카오 장소 검색: 실제로 부르지 않는다
os.environ["BUILDING_LOOKUP_LOG_PATH"] = ""
os.environ["BUILDING_LOOKUP_CALL_LOG_PATH"] = ""
os.environ["LISTING_STATUS_PATH"] = "off"  # 로컬 거래 가능 재확인 결과를 테스트에 섞지 않는다  # 로컬 .env.local 의 설정과 상관없이 설명 경로를 테스트한다


@pytest.fixture(autouse=True)
def _fresh_property_cache():
    load_properties.cache_clear()
    load_all_properties.cache_clear()
    load_status.cache_clear()
    load_walk_table.cache_clear()
    yield
    load_properties.cache_clear()
    load_walk_table.cache_clear()
