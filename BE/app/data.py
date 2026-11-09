import csv
import os
from functools import lru_cache
from pathlib import Path

from app.config import BE_DIR, _read
from app.models import Property

# BE/app/data.py 기준 ../../DB/poc_ewha.csv  (= BE 폴더 기준 ../DB/poc_ewha.csv)
DEFAULT_CSV_PATH = Path(__file__).resolve().parents[2] / "DB" / "poc_ewha.csv"

# 숫자로 변환할 열 (FE 타입에서 number | null)
NUMERIC_FIELDS = {
    name
    for name, field in Property.model_fields.items()
    if field.annotation == (float | None)
}


def _paths(value: str) -> list[Path]:
    """쉼표로 여러 파일을 줄 수 있다 (대학별 CSV). 상대 경로는 BE/ 기준."""
    out = []
    for part in value.split(","):
        part = part.strip()
        if part:
            path = Path(part)
            out.append(path if path.is_absolute() else (BE_DIR / path).resolve())
    return out


def _csv_paths() -> list[Path]:
    """DATA_CSV_PATH (프로세스 환경 → BE/.env → BE/.env.local 순, config 와 같은 규칙). 쉼표로 여러 파일."""
    override = _read("DATA_CSV_PATH")
    return _paths(override) if override else [DEFAULT_CSV_PATH]


def _csv_path() -> Path:
    """첫 번째 매물 CSV (예전 호출용)."""
    return _csv_paths()[0]


def _parse_row(row: dict[str, str]) -> Property:
    values: dict[str, object] = {}
    for key, raw in row.items():
        value = raw.strip() if raw is not None else ""
        if value == "":
            values[key] = None
        elif key in NUMERIC_FIELDS:
            values[key] = float(value)
        else:
            values[key] = value
    return Property(**values)


@lru_cache
def load_properties() -> tuple[Property, ...]:
    """모든 매물 CSV 를 순서대로 읽는다. 매물 ID 가 겹치면 조용히 덮지 않고 멈춘다
    (대학이 다른 같은 매물은 파이프라인이 ID 앞에 대학 키를 붙여 구분한다)."""
    out: list[Property] = []
    seen: dict[str, Path] = {}
    for path in _csv_paths():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                p = _parse_row(row)
                if p.property_id in seen:
                    raise ValueError(f"매물 ID 중복: {p.property_id} ({seen[p.property_id].name}, {path.name})")
                seen[p.property_id] = path
                out.append(p)
    return tuple(out)


def get_property(property_id: str) -> Property | None:
    return next((p for p in load_properties() if p.property_id == property_id), None)


# ---------------------------------------------------------------- 매물 × 강의동 도보시간 (긴 형식, 선택)

def _walk_table_paths() -> list[Path]:
    """WALK_TABLE_PATH (DATA_CSV_PATH 와 같은 규칙, 쉼표로 여러 파일). 없으면 매물 CSV 의 spot1~4 만 쓴다.

    프로세스 환경 변수에 빈 값으로 있으면 .env 파일 값과 상관없이 '표 없음'이다 (테스트에서 쓴다).
    """
    value = os.environ["WALK_TABLE_PATH"] if "WALK_TABLE_PATH" in os.environ else _read("WALK_TABLE_PATH")
    return _paths(value) if value else []


def _walk_table_path() -> Path | None:
    """첫 번째 도보시간 표 (예전 호출용)."""
    paths = _walk_table_paths()
    return paths[0] if paths else None


@lru_cache
def load_walk_table() -> tuple[tuple[str, str, float], ...]:
    """(property_id, building, walk_min) 목록. pipeline.build_walk_table 이 만든 CSV (여러 개면 이어 붙인다)."""
    rows: list[tuple[str, str, float]] = []
    for path in _walk_table_paths():
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows += [(r["property_id"], r["building"], float(r["walk_min"])) for r in csv.DictReader(f) if r.get("walk_min")]
    return tuple(rows)


def load_travel_times(properties) -> dict[str, dict[str, float]]:
    """매물 ID → 건물 이름 → 도보시간(분). 매물 CSV 의 spot1~4 에 도보시간 표를 더한다."""
    table: dict[str, dict[str, float]] = {}
    for p in properties:
        row = table.setdefault(p.property_id, {})
        for i in range(1, 5):
            name, walk = getattr(p, f"spot{i}_name"), getattr(p, f"spot{i}_walk_min")
            if name is not None and walk is not None:
                row[name] = walk
    for pid, building, walk in load_walk_table():
        if pid in table:
            table[pid][building] = walk
    return table
