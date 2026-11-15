"""테스트는 로컬 .env/.env.local 의 DATA_CSV_PATH 와 상관없이 팀 PoC 데이터(DB/poc_ewha.csv)로 돈다.

프로세스 환경 변수가 .env 파일보다 우선하므로(app.config._read) 여기서 기본 경로로 고정한다.
"""

import os

import pytest

from app.data import DEFAULT_CSV_PATH, load_properties, load_walk_table

os.environ["DATA_CSV_PATH"] = str(DEFAULT_CSV_PATH)
os.environ["WALK_TABLE_PATH"] = ""  # 빈 값 = 도보시간 표 없음 (spot1~4 만)
os.environ["AGENT_TRACE_PATH"] = ""  # 테스트에서 에이전트 턴 기록 파일을 쓰지 않는다
os.environ["AGENT_LLM_EXPLAIN"] = "on"  # 로컬 .env.local 의 설정과 상관없이 설명 경로를 테스트한다


@pytest.fixture(autouse=True)
def _fresh_property_cache():
    load_properties.cache_clear()
    load_walk_table.cache_clear()
    yield
    load_properties.cache_clear()
    load_walk_table.cache_clear()
