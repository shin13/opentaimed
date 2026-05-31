# path: tests/unit/test_dataset37.py
# brief: Verify Dataset 37 parser, on-disk cache, and download logic.

import asyncio
import io
import json
import os
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.models import DrugLicense
from taiwan_fda_mcp.sources.opendata.client import fetch_dataset37
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


def _make_zip(json_payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("37_5.json", json_payload)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_fetch_dataset37_unzips_and_parses(fixtures_dir):
    raw = (fixtures_dir / "dataset37_sample.json").read_bytes()
    zip_bytes = _make_zip(raw)

    async with respx.mock(base_url="https://data.fda.gov.tw") as router:
        router.get("/data/opendata/export/37/json").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        rows = await fetch_dataset37("https://data.fda.gov.tw")

    assert len(rows) == 4  # noqa: PLR2004
    assert rows[0].license_no == "衛署藥輸字第021571號"


@pytest.mark.asyncio
async def test_fetch_dataset37_raises_on_http_error():
    async with respx.mock(base_url="https://data.fda.gov.tw") as router:
        router.get("/data/opendata/export/37/json").mock(return_value=httpx.Response(500))
        with pytest.raises(DatasetFetchError):
            await fetch_dataset37("https://data.fda.gov.tw")


@pytest.mark.asyncio
async def test_fetch_dataset37_applies_rate_limit(fixtures_dir, monkeypatch):
    """fetch_dataset37 honours a good-citizen throttle so refreshes don't hammer the gov API."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    raw = (fixtures_dir / "dataset37_sample.json").read_bytes()
    zip_bytes = _make_zip(raw)

    async with respx.mock(base_url="https://data.fda.gov.tw") as router:
        router.get("/data/opendata/export/37/json").mock(
            return_value=httpx.Response(200, content=zip_bytes)
        )
        await fetch_dataset37("https://data.fda.gov.tw", rate_limit_interval=0.5)

    assert 0.5 in slept  # noqa: PLR2004


def test_parse_rows_maps_country(fixtures_dir):
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    by_license = {r.license_no: r for r in rows}
    assert by_license["衛署藥輸字第021571號"].country == "IT"
    assert by_license["衛署藥製字第040065號"].country == "TW"
