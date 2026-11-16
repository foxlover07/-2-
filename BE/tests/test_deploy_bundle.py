"""배포용 암호화 데이터 묶음 (app.deploy_bundle): 키가 있으면 풀고 경로를 맞추고, 없으면 아무것도 안 한다."""

import io
import os
import zipfile

from cryptography.fernet import Fernet

from app import deploy_bundle as db


def _bundle(tmp_path, key):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ewha_candidates_v3_student.csv", "a,b\n1,2\n")
        z.writestr("walk_times_v2.csv", "x\n")
        z.writestr("korea_buildings.csv", "y\n")
        z.writestr("../evil.csv", "z\n")  # 경로 탈출은 풀지 않는다
    p = tmp_path / "bundle.bin"
    p.write_bytes(Fernet(key).encrypt(buf.getvalue()))
    return p


def test_unpack_sets_paths(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    out = tmp_path / "out"
    for k in ("DATA_CSV_PATH", "WALK_TABLE_PATH", "BUILDING_REF_DIR"):
        monkeypatch.setenv(k, "/etc/secrets/old.csv")  # 대시보드에 남은 옛 값은 덮어쓴다
    monkeypatch.setenv("DATA_BUNDLE_KEY", key.decode())
    assert db.unpack_if_configured(_bundle(tmp_path, key), out) is True
    assert os.environ["DATA_CSV_PATH"].endswith("ewha_candidates_v3_student.csv")
    assert os.environ["WALK_TABLE_PATH"].endswith("walk_times_v2.csv")
    assert os.environ["BUILDING_REF_DIR"] == str(out)
    assert not (tmp_path / "evil.csv").exists() and (out / "korea_buildings.csv").exists()


def test_no_key_does_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_BUNDLE_KEY", raising=False)
    monkeypatch.setattr(db, "_read", lambda k: None)
    assert db.unpack_if_configured(_bundle(tmp_path, Fernet.generate_key()), tmp_path / "out") is False
    assert not (tmp_path / "out").exists()
