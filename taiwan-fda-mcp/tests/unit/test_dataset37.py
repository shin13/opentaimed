# path: tests/unit/test_dataset37.py
# brief: Verify Dataset 37 parser and on-disk cache logic.

import json
import os
import time
from pathlib import Path

import pytest  # noqa: F401  # imported for pytest plugin discovery

from taiwan_fda_mcp.models import DrugLicense
from taiwan_fda_mcp.sources.opendata.dataset37 import (
    cache_is_fresh,
    load_from_cache,
    parse_rows,
    write_to_cache,
)


def test_parse_rows_maps_chinese_keys(fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    assert len(rows) == 4  # noqa: PLR2004
    norvasc = next(r for r in rows if r.license_no == "衛署藥輸字第021571號")
    assert isinstance(norvasc, DrugLicense)
    assert norvasc.name_zh == "脈優錠５毫克"
    assert norvasc.name_en == "NORVASC TABLETS 5MG"
    assert norvasc.ingredient == "AMLODIPINE BESYLATE"


def test_write_then_load_cache_roundtrip(tmp_path: Path, fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    cache_dir = tmp_path / "cache"
    write_to_cache(rows, cache_dir)
    loaded = load_from_cache(cache_dir)
    assert loaded is not None
    assert len(loaded) == 4  # noqa: PLR2004
    assert loaded[0].license_no == rows[0].license_no


def test_load_returns_none_for_missing_cache(tmp_path: Path) -> None:
    assert load_from_cache(tmp_path / "does-not-exist") is None


def test_cache_freshness(tmp_path: Path, fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    cache_dir = tmp_path / "cache"
    write_to_cache(rows, cache_dir)
    assert cache_is_fresh(cache_dir, ttl_hours=24) is True
    # Force-age the cache by 25 hours
    cache_file = cache_dir / "dataset37.json"
    old = time.time() - 25 * 3600
    os.utime(cache_file, (old, old))
    assert cache_is_fresh(cache_dir, ttl_hours=24) is False
