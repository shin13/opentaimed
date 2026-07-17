# path: src/taiwan_fda_mcp/sources/opendata/dataset42.py
# brief: Parse Dataset 42 (藥品外觀) JSON rows and manage on-disk cache with TTL.

import json
import time
from pathlib import Path
from typing import Any

from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.models import DrugAppearance

_CACHE_FILE = "dataset42.json"

_FIELD_MAP: dict[str, str] = {
    "license_no": "許可證字號",
    "name_zh": "中文品名",
    "name_en": "英文品名",
    "shape": "形狀",
    "color": "顏色",
    "special_dosage_form": "特殊劑型",
    "odor": "特殊氣味",
    "score_line": "刻痕",
    "dimensions": "外觀尺寸",
    "imprint_1": "標註一",
    "imprint_2": "標註二",
    "image_url": "外觀圖檔連結",
}


def parse_rows(raw_rows: list[dict[str, Any]]) -> list[DrugAppearance]:
    """Map Chinese-keyed JSON rows into DrugAppearance models.

    Every mapped value is coerced to str ("" for None) — Dataset 42 stores
    外觀尺寸 as a number and 標註二 as null in places.
    """
    result: list[DrugAppearance] = []
    for row in raw_rows:
        kwargs: dict[str, str] = {}
        for field, source_key in _FIELD_MAP.items():
            value = row.get(source_key)
            kwargs[field] = "" if value is None else str(value)
        result.append(DrugAppearance(**kwargs))
    return result


def write_to_cache(rows: list[DrugAppearance], cache_dir: Path) -> None:
    """Persist the parsed rows to a JSON cache file."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump() for r in rows]
    (cache_dir / _CACHE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def load_from_cache(cache_dir: Path) -> list[DrugAppearance] | None:
    """Load cached rows or return None if the cache file is missing."""
    path = cache_dir / _CACHE_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            f"Corrupt dataset42 cache at {path}",
            detail={"error": str(exc)},
        ) from exc
    return [DrugAppearance(**row) for row in payload]


def cache_mtime(cache_dir: Path) -> float | None:
    """Epoch mtime of the cache file, or None if absent (real on-disk age)."""
    path = cache_dir / _CACHE_FILE
    return path.stat().st_mtime if path.exists() else None


def cache_is_fresh(cache_dir: Path, ttl_hours: int) -> bool:
    """True if the cache file exists and was written within ttl_hours."""
    path = cache_dir / _CACHE_FILE
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < ttl_hours * 3600
