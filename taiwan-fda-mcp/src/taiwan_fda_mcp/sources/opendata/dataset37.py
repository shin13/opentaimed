# path: src/taiwan_fda_mcp/sources/opendata/dataset37.py
# brief: Parse Dataset 37 JSON rows and manage on-disk cache with TTL.

import json
import time
from pathlib import Path
from typing import Any

from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.models import DrugLicense

_CACHE_FILE = "dataset37.json"

_FIELD_MAP: dict[str, str] = {
    "license_no": "許可證字號",
    "name_zh": "中文品名",
    "name_en": "英文品名",
    "indication": "適應症",
    "form": "劑型",
    "ingredient": "主成分略述",
    "applicant": "申請商名稱",
    "manufacturer": "製造商名稱",
    "country": "製造廠國別",
    "drug_class": "藥品類別",
    "cancel_status": "註銷狀態",
    "valid_until": "有效日期",
    "last_change_date": "異動日期",
}


def parse_rows(raw_rows: list[dict[str, Any]]) -> list[DrugLicense]:
    """Map Chinese-keyed JSON rows into DrugLicense models."""
    result: list[DrugLicense] = []
    for row in raw_rows:
        kwargs: dict[str, Any] = {}
        for field, source_key in _FIELD_MAP.items():
            value = row.get(source_key)
            if field == "drug_class":
                kwargs[field] = value  # may be None
            else:
                kwargs[field] = value if value is not None else ""
        result.append(DrugLicense(**kwargs))
    return result


def write_to_cache(rows: list[DrugLicense], cache_dir: Path) -> None:
    """Persist the parsed rows to a JSON cache file."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump() for r in rows]
    (cache_dir / _CACHE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def load_from_cache(cache_dir: Path) -> list[DrugLicense] | None:
    """Load cached rows or return None if cache is missing/corrupt."""
    path = cache_dir / _CACHE_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            f"Corrupt dataset37 cache at {path}",
            detail={"error": str(exc)},
        ) from exc
    return [DrugLicense(**row) for row in payload]


def cache_is_fresh(cache_dir: Path, ttl_hours: int) -> bool:
    """True if the cache file exists and was written within ttl_hours."""
    path = cache_dir / _CACHE_FILE
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_hours * 3600


def cache_mtime(cache_dir: Path) -> float | None:
    """Epoch mtime of the cache file, or None if absent.

    Used by the refresh loader to compute the real age of a disk cache on first
    load (so a server restart picks up a genuinely-stale on-disk cache).
    """
    path = cache_dir / _CACHE_FILE
    return path.stat().st_mtime if path.exists() else None
