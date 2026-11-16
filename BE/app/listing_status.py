"""매물 거래 가능 여부 재확인 결과 (pipeline.check_listing_status) 읽기.

- DB/staging/listing_status_*.csv 를 모두 읽고 매물(source, source_listing_id)마다 가장 최근 판정을 쓴다.
  단, 'error'(판정 보류)는 이전 판정을 덮지 않는다.
- LISTING_STATUS_PATH 로 파일을 정할 수 있다 (쉼표, 상대 경로는 BE/ 기준). 'off' 면 확인 결과를 쓰지 않는다.
- 'unavailable' 매물은 추천·지도 목록에서 뺀다 (app.data.load_properties). 상세 화면에는 상태를 보여 준다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT, _read


@dataclass(frozen=True)
class ListingStatus:
    status: str              # active | unavailable | error
    upstream_status: str | None
    checked_at: str | None


def _paths() -> list[Path]:
    override = (_read("LISTING_STATUS_PATH") or "").strip()
    if override.lower() == "off":
        return []
    if override:
        return [Path(p.strip()) if Path(p.strip()).is_absolute() else (REPO_ROOT / "BE" / p.strip()).resolve()
                for p in override.split(",") if p.strip()]
    return sorted((REPO_ROOT / "DB" / "staging").glob("listing_status_*.csv"))


@lru_cache
def load_status() -> dict[tuple[str, str], ListingStatus]:
    out: dict[tuple[str, str], ListingStatus] = {}
    for path in _paths():
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (r.get("source", ""), r.get("source_listing_id", ""))
                new = ListingStatus(r.get("status") or "error", r.get("upstream_status") or None, r.get("checked_at") or None)
                old = out.get(key)
                if old and new.status == "error" and old.status != "error":
                    continue
                if old and old.checked_at and new.checked_at and new.checked_at < old.checked_at:
                    continue
                out[key] = new
    return out
