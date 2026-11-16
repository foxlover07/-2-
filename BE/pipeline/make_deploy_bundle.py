"""배포용 데이터 묶음 만들기 (app.deploy_bundle 참고). API 호출 없음.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.make_deploy_bundle <파일 폴더> [--key-file <키 저장 경로>]

- 폴더의 *_candidates_*.csv · *walk_times*.csv · *_buildings.csv 를 zip → Fernet 암호화 → BE/data/deploy_bundle.bin
- 새 키를 만들어 --key-file 에만 쓴다 (화면·저장소에 남기지 않음). Render 환경 변수 DATA_BUNDLE_KEY 에 그 값을 넣는다.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet

from app.deploy_bundle import BUNDLE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--key-file", required=True)
    a = ap.parse_args()
    src = Path(a.folder)
    files = sorted(p for p in src.glob("*.csv")
                   if "_candidates_" in p.name or "walk_times" in p.name or p.name.endswith("_buildings.csv")
                   or p.name.startswith(("rent_tx_", "building_titles_", "stores_")))
    if not files:
        raise SystemExit(f"{src} 에 넣을 csv 가 없습니다.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.name)
    kf = Path(a.key_file)
    # 키 파일이 이미 있으면 그 키를 다시 쓴다 (배포 환경 변수 DATA_BUNDLE_KEY 를 바꾸지 않아도 되게)
    key = kf.read_text(encoding="utf-8").strip().encode() if kf.exists() and kf.read_text(encoding="utf-8").strip() else Fernet.generate_key()
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_bytes(Fernet(key).encrypt(buf.getvalue()))
    kf.write_text(key.decode(), encoding="utf-8")
    print(f"{len(files)}개 파일 → {BUNDLE} ({BUNDLE.stat().st_size // 1024}KB). 키는 {a.key_file} 에만 저장했습니다.")


if __name__ == "__main__":
    main()
