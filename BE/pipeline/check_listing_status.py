"""광고 매물이 아직 거래 가능한지 다시 확인 (jibsa 상세 조회) → 거래 끝난 매물을 추천·지도에서 뺀다.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.check_listing_status [--limit 20] [--stale-hours 20]

- 대상: DATA_CSV_PATH 매물의 (source, source_listing_id) 고유 목록. 지금은 직방만 (jibsa zigbang listing <id>).
- 한 번에 하나씩 순서대로 부른다 (jibsa 가 소스별 요청 간격·분당 상한을 지킨다. 병렬 호출 금지).
  CLI 는 실행마다 시작에 약 4초가 들어, jibsa 설치 환경 파이썬이 있으면 한 프로세스에서 차례로 부른다
  (pipeline/jibsa_batch_listing.py, 건당 약 1~2초). 없으면 CLI 를 한 건씩 부른다. JIBSA_PYTHON 으로 경로 지정.
- --stale-hours: 최근 확인 결과가 이 시간 안이면 건너뛴다 (하루 한 번 돌리면 이어서 할 수 있다).
- 판정:
    active      상세 조회 성공 (현재 거래 가능으로 나옴. 광고 기준이며 실제 계약 가능 여부는 보장하지 않음)
    unavailable jibsa LISTING_UNAVAILABLE (직방 status=close·cancel 등: 만료·거래완료·숨김) → 추천·지도에서 뺀다
    error       그 밖의 실패 (네트워크·요청 제한 등) → 판정 보류, 빼지 않는다
- 상세 조회가 되면 그때의 보증금·월세·관리비도 기록한다 (광고 가격이 바뀌었는지 보고만, 매물 CSV 는 고치지 않는다).
- 출력: 원본 DB/raw/jibsa_status/<ts>.jsonl (append-only, 커밋 금지) · DB/staging/listing_status_<ts>.csv + .report.json
  앱은 DB/staging/listing_status_*.csv 를 모두 읽어 매물마다 가장 최근 확인 결과를 쓴다 (app.listing_status).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import threading
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from app.config import REPO_ROOT
from app.listing_status import load_status

DB = REPO_ROOT / "DB"
FIELDS = ["source", "source_listing_id", "status", "upstream_status", "deposit_manwon", "monthly_rent_manwon",
          "maintenance_manwon", "checked_at", "error_code", "raw_file"]


def _targets() -> list[tuple[str, str]]:
    from app.data import _csv_paths  # noqa: PLC0415

    seen: dict[tuple[str, str], None] = {}
    for path in _csv_paths():
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("source") and r.get("source_listing_id"):
                    seen.setdefault((r["source"], r["source_listing_id"]), None)
    return list(seen)


def check_one(jibsa: str, source: str, lid: str) -> tuple[dict, str]:
    """jibsa CLI 상세 조회 1건 → (기록 행 일부, 원본 텍스트)."""
    p = subprocess.run([jibsa, source, "listing", lid, "--compact"], capture_output=True, text=True, encoding="utf-8",
                       timeout=120)
    raw = p.stdout.strip() or p.stderr.strip()
    return parse(raw, p.returncode), raw


def parse(raw: str, returncode: int = 0) -> dict:
    """jibsa 출력(JSON 텍스트) → 판정. 성공 JSON 이면 active, LISTING_UNAVAILABLE 이면 unavailable, 그 밖은 error."""
    row: dict = {"status": "error", "upstream_status": None, "error_code": None}
    try:
        d = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        row["error_code"] = "BAD_JSON"
        return row
    if not d:
        row["error_code"] = "EMPTY"
        return row
    if returncode == 0 and "error" not in d:
        row["status"] = "active"
        row["upstream_status"] = d.get("status")
        row["deposit_manwon"] = d.get("deposit_manwon")
        row["monthly_rent_manwon"] = d.get("rent_manwon")
        row["maintenance_manwon"] = d.get("manage_cost_manwon")
    else:
        err = d.get("error", {})
        row["error_code"] = err.get("code") or f"EXIT_{returncode}"
        if row["error_code"] == "LISTING_UNAVAILABLE":
            row["status"] = "unavailable"
            row["upstream_status"] = err.get("listing_status")
    return row


def _jibsa_python() -> Path | None:
    env = os.environ.get("JIBSA_PYTHON")
    cands = [Path(env)] if env else []
    if os.name == "nt" and os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "uv" / "tools" / "jibsa" / "Scripts" / "python.exe")
    cands.append(Path.home() / ".local" / "share" / "uv" / "tools" / "jibsa" / "bin" / "python")
    return next((c for c in cands if c.exists()), None)


def run_checks(jibsa: str, todo: list[tuple[str, str]]) -> Iterator[tuple[str, str, dict, str]]:
    """(source, id, 판정, 원본) 를 차례로 낸다. 한 프로세스 도우미가 있으면 그것으로, 없으면 CLI 한 건씩."""
    py = _jibsa_python()
    if py is None:
        for src, lid in todo:
            try:
                row, raw = check_one(jibsa, src, lid)
            except subprocess.TimeoutExpired:
                row, raw = {"status": "error", "error_code": "TIMEOUT", "upstream_status": None}, ""
            yield src, lid, row, raw
        return
    helper = Path(__file__).with_name("jibsa_batch_listing.py")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    with subprocess.Popen([str(py), "-I", "-X", "utf8", str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          text=True, encoding="utf-8", env=env) as proc:
        assert proc.stdin and proc.stdout
        stdin = proc.stdin

        def feed() -> None:  # 한꺼번에 쓰면 파이프가 차서 서로 기다리며 멈춘다 → 별도 스레드로 한 줄씩 넣는다
            for s, i in todo:
                stdin.write(f"{s} {i}\n")
                stdin.flush()
            stdin.close()

        threading.Thread(target=feed, daemon=True).start()
        for line in proc.stdout:
            d = json.loads(line)
            yield d["source"], d["id"], parse(d["out"]), d["out"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="이번에 확인할 최대 건수 (0 = 전부)")
    ap.add_argument("--stale-hours", type=float, default=20)
    a = ap.parse_args()
    jibsa = shutil.which("jibsa")
    if not jibsa:
        raise SystemExit("jibsa CLI 가 없습니다 (https://github.com/Rukkakka/jibsa).")
    prev = load_status()
    cutoff = datetime.now().astimezone() - timedelta(hours=a.stale_hours)
    todo = []
    for src, lid in _targets():
        p = prev.get((src, lid))
        if p and p.checked_at and datetime.fromisoformat(p.checked_at) >= cutoff and p.status != "error":
            continue
        if src != "zigbang":
            continue
        todo.append((src, lid))
    if a.limit:
        todo = todo[: a.limit]
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_path = DB / "raw" / "jibsa_status" / f"{ts}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    out = DB / "staging" / f"listing_status_{ts}.csv"
    counts: Counter = Counter()
    upstream: Counter = Counter()
    print(f"확인 대상 {len(todo)}건 (최근 {a.stale_hours:g}시간 안에 확인한 매물은 건너뜀)")
    with out.open("w", encoding="utf-8-sig", newline="") as f, raw_path.open("a", encoding="utf-8") as rawf:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i, (src, lid, row, raw) in enumerate(run_checks(jibsa, todo), 1):
            checked = datetime.now().astimezone().isoformat(timespec="seconds")
            rawf.write(json.dumps({"source": src, "id": lid, "checked_at": checked, "raw": raw}, ensure_ascii=False) + "\n")
            w.writerow({"source": src, "source_listing_id": lid, "checked_at": checked,
                        "raw_file": str(raw_path.relative_to(DB)).replace("\\", "/"), **row})
            f.flush()
            counts[row["status"]] += 1
            upstream[f"{row['status']}:{row.get('upstream_status')}"] += 1
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} {dict(counts)}", flush=True)
            if row.get("error_code") == "RATE_LIMITED":
                print("요청 제한에 걸려 멈춥니다. 나중에 다시 실행하면 이어서 확인합니다.")
                break
    report = {"ts": ts, "checked": sum(counts.values()), "by_status": dict(counts), "by_upstream": dict(upstream),
              "output": str(out.relative_to(REPO_ROOT)).replace("\\", "/"),
              "note": "unavailable = 직방에서 거래할 수 없는 매물(만료·거래완료·숨김). active 도 광고 기준이며 실제 계약 가능은 보장하지 않음."}
    out.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
