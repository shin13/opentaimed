# path: tests/unit/test_appearance_store.py
# brief: Verify the non-blocking Dataset 42 in-memory index.

import io
import json
import zipfile

import httpx
import pytest
import respx

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.sources.opendata.appearance_store import AppearanceStore


def _settings(tmp_path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATASET42_CACHE_DIR=tmp_path,
        DATASET42_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )


def _zip_json(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("42_5.json", json.dumps(rows, ensure_ascii=False))
    return buf.getvalue()


@pytest.mark.asyncio
@respx.mock
async def test_cold_load_downloads_and_indexes(tmp_path):
    rows = [{"許可證字號": "L1", "中文品名": "藥", "形狀": "圓形"}]
    route = respx.get("https://data.fda.gov.tw/data/opendata/export/42/json").mock(
        return_value=httpx.Response(200, content=_zip_json(rows))
    )
    store = AppearanceStore()
    index = await store.get_index(_settings(tmp_path))
    assert "L1" in index
    assert index["L1"].shape == "圓形"
    assert route.call_count == 1
    # second call hits the fresh memo — no new download
    await store.get_index(_settings(tmp_path))
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_cold_load_raises_when_no_snapshot(tmp_path):
    respx.get("https://data.fda.gov.tw/data/opendata/export/42/json").mock(
        return_value=httpx.Response(500)
    )
    store = AppearanceStore()
    with pytest.raises(DatasetFetchError):
        await store.get_index(_settings(tmp_path))


@pytest.mark.asyncio
@respx.mock
async def test_freshness_reports_loaded(tmp_path):
    respx.get("https://data.fda.gov.tw/data/opendata/export/42/json").mock(
        return_value=httpx.Response(200, content=_zip_json([{"許可證字號": "L1"}]))
    )
    store = AppearanceStore()
    s = _settings(tmp_path)
    await store.get_index(s)
    retrieved_at, age_hours, is_stale = store.freshness(s)
    assert retrieved_at is not None
    assert age_hours is not None
    assert age_hours >= 0
    assert is_stale is False
