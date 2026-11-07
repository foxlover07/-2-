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


def _v2_zigbang_ewha9() -> list[tuple[str, list[str]]]:
    """직방만 재수집 (2026-11-08): 서대문구 6개 동 + 이대 옆 마포구 3개 동, 동마다 끝까지 이어 받는다.

    직방만 목록에 지번 주소를 줘서 Kakao 주소 검색·도보 경로를 붙일 수 있다 (네이버·다방·당근은 동 이름까지).
    페이지는 has_more/next 로 이어 받고, 동마다 최대 MAX_PAGES 페이지까지만 받는다.
    """
    regions = [f"서대문구 {d}" for d in EWHA_DONGS] + [f"마포구 {d}" for d in MAPO_DONGS]
    return [
        (f"zigbang:{r.split()[-1]}", ["zigbang", "search", "listings", "room", r, "--trade", "월세", "--limit", "50"])
        for r in regions
    ]


# 이화여대 서쪽·남쪽으로 붙은 마포구 법정동 (이대역·신촌역 생활권)
MAPO_DONGS = ["노고산동", "염리동", "대흥동"]

# 이어 받기(--next) 할 계획과 동마다 최대 페이지 수
PAGINATED_PLANS = {"v2_zigbang_ewha9"}
MAX_PAGES = 4

PLANS = {"v0_pilot": _v0_pilot, "v1_ewha6": _v1_ewha6, "v2_zigbang_ewha9": _v2_zigbang_ewha9}


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
    todo = [(key, args, 1) for key, args in queries]
    while todo:
        key, base_args, page = todo.pop(0)
        args = [*base_args, "--compact"]
        if page > 1:
            key = f"{key}#p{page}"
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

        # 이어 받기: 같은 명령·조건에 --next 만 붙인다 (jibsa 가이드). 실패하면 그 동은 거기서 멈춘다.
        if plan in PAGINATED_PLANS and proc.returncode == 0 and page < MAX_PAGES:
            try:
                data = json.loads(proc.stdout)
            except ValueError:
                data = {}
            if data.get("has_more") and data.get("next"):
                first_key = key.split("#")[0]
                no_next = base_args[: base_args.index("--next")] if "--next" in base_args else base_args
                todo.insert(0, (first_key, [*no_next, "--next", str(data["next"])], page + 1))

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
