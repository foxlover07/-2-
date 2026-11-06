"""jibsa 수집. 원본 stdout/stderr 를 바이트 그대로 보존하고 manifest 에 실행 기록을 남긴다.

사용 (BE/ 에서):
    home/Scripts/python.exe -m pipeline.jibsa_collect <plan> [query_key ...]

- 명령은 순서대로 하나씩 실행한다 (jibsa 가이드: 병렬 호출 금지).
- 실패해도 재시도하지 않고 기록만 한다.
- 결과는 DB/raw/jibsa/<수집시각>/ 에 쓰고, 기존 폴더는 덮어쓰지 않는다 (append-only).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.config import REPO_ROOT

RAW_ROOT = REPO_ROOT / "DB" / "raw" / "jibsa"

# 이화여대 주변 서대문구 법정동. 직방·다방은 '대현동' 만 주면 대구 북구와 겹치므로 구 이름을 붙인다.
EWHA_DONGS = ["대현동", "대신동", "신촌동", "봉원동", "북아현동", "창천동"]


def _v0_pilot() -> list[tuple[str, list[str]]]:
    """1차 시험 (20261106T204711, 204802): 대현동, 소스별 5건."""
    return [
        ("zigbang", ["zigbang", "search", "listings", "room", "서대문구 대현동", "--trade", "월세", "--limit", "5"]),
        ("naver", ["naver", "search", "listings", "서대문구 대현동", "-t", "원룸,빌라,오피스텔", "--trade", "월세", "--limit", "5"]),
        ("daangn", ["daangn", "search", "listings", "대현동", "--city", "서대문구", "-t", "원룸,투룸,오피스텔", "--trade", "월세", "--limit", "5"]),
        ("dabang", ["dabang", "search", "listings", "room", "서대문구 대현동", "--trade", "월세", "--limit", "5"]),
    ]


def _v1_ewha6() -> list[tuple[str, list[str]]]:
    """확대 수집: 6개 동 × (직방·네이버·다방) 동마다 20건 + 당근 이대 반경 1km 60건.

    당근은 동 이름 검색이 주변 동을 섞어 주므로(in_regions false) 장소 반경 검색으로 바꾼다.
    """
    queries: list[tuple[str, list[str]]] = []
    for dong in EWHA_DONGS:
        region = f"서대문구 {dong}"
        queries += [
            (f"zigbang:{dong}", ["zigbang", "search", "listings", "room", region, "--trade", "월세", "--limit", "20"]),
            (f"naver:{dong}", ["naver", "search", "listings", region, "-t", "원룸,빌라,오피스텔", "--trade", "월세", "--limit", "20"]),
            (f"dabang:{dong}", ["dabang", "search", "listings", "room", region, "--trade", "월세", "--limit", "20"]),
        ]
    queries.append(
        ("daangn:이화여자대학교_r1km",
         ["daangn", "search", "listings", "이화여자대학교", "--radius", "1", "-t", "원룸,투룸,오피스텔", "--trade", "월세", "--limit", "60"])
    )
    return queries


PLANS = {"v0_pilot": _v0_pilot, "v1_ewha6": _v1_ewha6}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe(key: str) -> str:
    return key.replace(":", "__")


def collect(plan: str, only: list[str] | None = None) -> Path:
    exe = shutil.which("jibsa")
    if exe is None:
        raise SystemExit("jibsa 를 찾을 수 없다")
    version = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()

    queries = [q for q in PLANS[plan]() if not only or q[0] in only]
    out_dir = RAW_ROOT / datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=False)

    runs = []
    for key, args in queries:
        args = [*args, "--compact"]
        requested_at = _now()
        proc = subprocess.run([exe, *args], capture_output=True)
        stdout_file = f"{_safe(key)}.stdout.json"
        stderr_file = f"{_safe(key)}.stderr.json" if proc.stderr else None
        (out_dir / stdout_file).write_bytes(proc.stdout)
        if stderr_file:
            (out_dir / stderr_file).write_bytes(proc.stderr)
        runs.append({
            "key": key,
            "source": args[0],
            "args": args,
            "requested_at": requested_at,
            "finished_at": _now(),
            "exit_code": proc.returncode,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
        })
        print(f"{key}: exit={proc.returncode} stdout={len(proc.stdout)}B stderr={len(proc.stderr)}B", flush=True)

    manifest = {
        "tool": "jibsa",
        "tool_version": version,
        "plan": plan,
        "purpose": "dev: 이화여대 인근 매물 수집",
        "data_class": "development",
        "runs": runs,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2 or sys.argv[1] not in PLANS:
        raise SystemExit(f"사용: python -m pipeline.jibsa_collect <{'|'.join(PLANS)}> [query_key ...]")
    print(collect(sys.argv[1], sys.argv[2:] or None))
