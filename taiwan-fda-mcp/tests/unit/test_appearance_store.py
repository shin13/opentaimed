# path: tests/unit/test_appearance_store.py
# brief: Verify the non-blocking Dataset 42 in-memory index.

import asyncio
import io
import json
import zipfile

import httpx
import pytest
import respx

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.sources.opendata.appearance_store import AppearanceStore

_DATASET42_URL = "https://data.fda.gov.tw/data/opendata/export/42/json"

# Written by AppearanceStore._cold_load / _background_reload via
# sources.opendata.dataset42.write_to_cache.
_CACHE_FILE = "dataset42.json"


def _settings(tmp_path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        DATASET42_CACHE_DIR=tmp_path,
        DATASET42_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )


def _stale_settings(tmp_path) -> Settings:
    """Settings under which every memo is stale.

    A 0-hour TTL suffices because `_is_stale` compares the memo's age with
    `>=`, so a memo loaded this very instant already counts as past its TTL.
    """
    return Settings(  # type: ignore[call-arg]
        DATASET42_CACHE_DIR=tmp_path,
        DATASET42_TTL_HOURS=0,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )


def _zip_json(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("42_5.json", json.dumps(rows, ensure_ascii=False))
    return buf.getvalue()


async def _await_scheduled_reload(store: AppearanceStore) -> None:
    """Run the store's scheduled background reload to completion.

    The reload is deliberately fire-and-forget (ADR-0013), so there is no
    public handle on it. Reading the task — never mutating it — keeps these
    tests deterministic instead of sleeping for a guessed interval.
    """
    task = store._refresh_task
    assert task is not None, "expected get_index to schedule a background reload"
    await task


@pytest.mark.asyncio
@respx.mock
async def test_cold_load_downloads_and_indexes(tmp_path):
    rows = [{"許可證字號": "L1", "中文品名": "藥", "形狀": "圓形"}]
    route = respx.get(_DATASET42_URL).mock(
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
    respx.get(_DATASET42_URL).mock(return_value=httpx.Response(500))
    store = AppearanceStore()
    with pytest.raises(DatasetFetchError):
        await store.get_index(_settings(tmp_path))


@pytest.mark.asyncio
@respx.mock
async def test_freshness_reports_loaded(tmp_path):
    respx.get(_DATASET42_URL).mock(
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


@pytest.mark.asyncio
@respx.mock
async def test_stale_memo_is_served_without_awaiting_the_network(tmp_path):
    """ADR-0013's central promise: past the TTL, the query itself does not wait."""
    route = respx.get(_DATASET42_URL).mock(
        return_value=httpx.Response(200, content=_zip_json([{"許可證字號": "L1"}]))
    )
    store = AppearanceStore()
    stale = _stale_settings(tmp_path)
    await store.get_index(stale)  # cold load
    after_cold_load = route.call_count
    assert after_cold_load == 1

    index = await store.get_index(stale)

    # get_index does not yield after create_task, so the reload has not run a
    # single line yet: the stale rows came back with no second request issued.
    assert "L1" in index
    assert route.call_count == after_cold_load
    assert store._refresh_task is not None

    await _await_scheduled_reload(store)
    assert route.call_count == after_cold_load + 1


@pytest.mark.asyncio
@respx.mock
async def test_background_reload_swaps_in_fresh_rows(tmp_path):
    route = respx.get(_DATASET42_URL).mock(
        side_effect=[
            httpx.Response(200, content=_zip_json([{"許可證字號": "L1", "形狀": "圓形"}])),
            httpx.Response(200, content=_zip_json([{"許可證字號": "L2", "形狀": "橢圓形"}])),
        ]
    )
    store = AppearanceStore()
    cold = await store.get_index(_stale_settings(tmp_path))
    assert set(cold) == {"L1"}
    after_cold_load = route.call_count

    await store.get_index(_stale_settings(tmp_path))  # stale → schedule the reload
    await _await_scheduled_reload(store)
    assert route.call_count == after_cold_load + 1
    after_reload = route.call_count

    # The reload stamped a new _loaded_at, so a 24h TTL now takes the fast path:
    # the swapped-in rows come back with no further request.
    fresh = await store.get_index(_settings(tmp_path))
    assert set(fresh) == {"L2"}
    assert fresh["L2"].shape == "橢圓形"
    assert route.call_count == after_reload


@pytest.mark.asyncio
@respx.mock
async def test_failed_background_reload_keeps_stale_memo(tmp_path):
    route = respx.get(_DATASET42_URL).mock(
        side_effect=[
            httpx.Response(200, content=_zip_json([{"許可證字號": "L1", "形狀": "圓形"}])),
            httpx.Response(500),
        ]
    )
    store = AppearanceStore()
    await store.get_index(_stale_settings(tmp_path))  # cold load
    after_cold_load = route.call_count

    await store.get_index(_stale_settings(tmp_path))  # stale → schedule the reload
    # The DatasetFetchError is swallowed inside the task, so this must not raise.
    await _await_scheduled_reload(store)
    assert route.call_count == after_cold_load + 1  # the reload ran, and failed
    after_reload = route.call_count

    # Make the memo the only possible source of rows: had the failed reload
    # cleared _index, the next get_index would cold-load, find no snapshot on
    # disk, and issue a third request (which the side_effect list cannot serve).
    (tmp_path / _CACHE_FILE).unlink()

    kept = await store.get_index(_settings(tmp_path))
    assert set(kept) == {"L1"}
    assert kept["L1"].shape == "圓形"
    assert route.call_count == after_reload


@pytest.mark.asyncio
@respx.mock
async def test_two_stale_queries_schedule_only_one_reload(tmp_path):
    route = respx.get(_DATASET42_URL).mock(
        return_value=httpx.Response(200, content=_zip_json([{"許可證字號": "L1"}]))
    )
    store = AppearanceStore()
    stale = _stale_settings(tmp_path)
    await store.get_index(stale)  # cold load
    after_cold_load = route.call_count

    await store.get_index(stale)
    first_task = store._refresh_task
    await store.get_index(stale)  # still stale, and the first reload is still pending

    assert store._refresh_task is first_task  # single in-flight guard held
    await _await_scheduled_reload(store)
    assert route.call_count == after_cold_load + 1  # exactly one reload, not two


@pytest.mark.asyncio
@respx.mock
async def test_shutdown_cancels_in_flight_reload_and_is_idempotent(tmp_path):
    entered_reload = asyncio.Event()
    requests: list[httpx.Request] = []

    async def _first_ok_then_hang(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            rows = [{"許可證字號": "L1", "形狀": "圓形"}]
            return httpx.Response(200, content=_zip_json(rows))
        entered_reload.set()
        await asyncio.Event().wait()  # hangs until shutdown() cancels the task
        raise AssertionError("unreachable: the hanging fetch must be cancelled")

    respx.get(_DATASET42_URL).mock(side_effect=_first_ok_then_hang)
    store = AppearanceStore()
    stale = _stale_settings(tmp_path)
    await store.get_index(stale)  # cold load
    await store.get_index(stale)  # stale → schedule the reload

    task = store._refresh_task
    assert task is not None
    await entered_reload.wait()  # the reload is genuinely mid-fetch now
    assert not task.done()

    await store.shutdown()
    assert task.cancelled()
    assert store._refresh_task is None

    await store.shutdown()  # idempotent — nothing in flight left to cancel
    await AppearanceStore().shutdown()  # and safe on a store that never refreshed

    # Cancelling the reload left the memo intact (see the unlink rationale above).
    (tmp_path / _CACHE_FILE).unlink()
    kept = await store.get_index(_settings(tmp_path))
    assert set(kept) == {"L1"}
