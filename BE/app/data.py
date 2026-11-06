import csv
import os
from functools import lru_cache
from pathlib import Path

from app.models import Property

# BE/app/data.py 기준 ../../DB/poc_ewha.csv  (= BE 폴더 기준 ../DB/poc_ewha.csv)
DEFAULT_CSV_PATH = Path(__file__).resolve().parents[2] / "DB" / "poc_ewha.csv"

# 숫자로 변환할 열 (FE 타입에서 number | null)
NUMERIC_FIELDS = {
    name
    for name, field in Property.model_fields.items()
    if field.annotation == (float | None)
}


def _csv_path() -> Path:
    override = os.getenv("DATA_CSV_PATH")
    return Path(override) if override else DEFAULT_CSV_PATH


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
    with _csv_path().open(encoding="utf-8-sig", newline="") as f:
        return tuple(_parse_row(row) for row in csv.DictReader(f))


def get_property(property_id: str) -> Property | None:
    return next((p for p in load_properties() if p.property_id == property_id), None)
