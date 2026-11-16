"""배포용 데이터 묶음 (암호화) — 2026-11-16 제출 배포용.

수집 매물·도보시간·건물 기준표는 공개 저장소에 그대로 올리지 않는다 (CLAUDE.md, 11-09 결정).
대신 zip 을 Fernet(cryptography)으로 암호화한 BE/data/deploy_bundle.bin 만 저장소에 두고,
서버가 시작할 때 DATA_BUNDLE_KEY(Render 환경 변수)로 풀어 BE/.deploy_data/ 에 둔 뒤 데이터 경로를 그쪽으로 맞춘다.
- 키가 없거나 묶음 파일이 없으면 아무것도 하지 않는다 (로컬 개발·테스트는 기존 .env.local 경로 그대로).
- 키는 저장소·로그에 남기지 않는다. 만들기: python -m pipeline.make_deploy_bundle
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from app.config import BE_DIR, _read

BUNDLE = BE_DIR / "data" / "deploy_bundle.bin"
OUT = BE_DIR / ".deploy_data"


def unpack_if_configured(bundle: Path = BUNDLE, out: Path = OUT) -> bool:
    """키와 묶음이 있으면 풀고 DATA_CSV_PATH·WALK_TABLE_PATH·BUILDING_REF_DIR 를 맞춘다. 풀었으면 True."""
    key = (os.getenv("DATA_BUNDLE_KEY") or _read("DATA_BUNDLE_KEY") or "").strip()
    if not key or not bundle.exists():
        return False
    from cryptography.fernet import Fernet

    raw = Fernet(key.encode()).decrypt(bundle.read_bytes())
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if "/" in name or "\\" in name or name.startswith("."):  # 평평한 파일만 (경로 탈출 방지)
                continue
            (out / name).write_bytes(z.read(name))
    files = sorted(p.name for p in out.glob("*.csv"))
    os.environ["DATA_CSV_PATH"] = ",".join(str(out / f) for f in files if "_candidates_" in f)
    os.environ["WALK_TABLE_PATH"] = ",".join(str(out / f) for f in files if "walk_times" in f)
    os.environ["BUILDING_REF_DIR"] = str(out)
    # 참고 정보 (공공데이터 미리 수집분): 실거래·건축물대장·상가정보. 묶음에 있을 때만
    for env, part in (("MARKET_TX_PATH", "rent_tx_"), ("BUILDING_TITLES_PATH", "building_titles_"), ("STORES_PATH", "stores_")):
        hit = [str(out / f) for f in files if f.startswith(part)]
        if hit:
            os.environ[env] = ",".join(hit)
    return True
