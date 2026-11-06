"""데이터 파이프라인·외부 API 설정.

팀 BE 와 같이 python-dotenv 로 읽는다. BE/.env 다음 BE/.env.local 순서이며 뒤의 값이 이긴다.
이미 프로세스 환경 변수에 있는 값은 덮어쓰지 않는다.
키 값은 로그·응답·예외에 절대 출력하지 않는다.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

BE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BE_DIR.parent


@dataclass(frozen=True)
class Settings:
    kakao_rest_api_key: str | None
    db_dir: Path


def _read(name: str) -> str | None:
    if os.getenv(name):
        return os.getenv(name)
    value = None
    for env_file in (BE_DIR / ".env", BE_DIR / ".env.local"):
        if env_file.exists():
            value = dotenv_values(env_file).get(name) or value
    return value


def get_settings() -> Settings:
    return Settings(kakao_rest_api_key=_read("KAKAO_REST_API_KEY"), db_dir=REPO_ROOT / "DB")
