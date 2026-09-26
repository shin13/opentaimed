# path: tests/unit/test_nhi_outage.py
# brief: What a user gets from the NHI tools while info.nhi.gov.tw is unreachable.

"""Behaviour of the NHI tools during an NHI outage, one scenario per test.

The outage is simulated the way it really happens (#113): the connection is
reset mid-read, so httpx raises `ReadError`. That is a transport error, which
takes the probe's retry path. A mocked HTTP 500 would not: it is a
different error class and is never retried.

These tests pin current behaviour. Several of them describe costs a user pays
(blocking, repeated downloads), not behaviour anyone chose on purpose.
"""

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

import taiwan_fda_mcp.mcp_server as srv
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.models import NhiCacheMeta
from taiwan_fda_mcp.sources.nhi import client as nhi_client
from taiwan_fda_mcp.sources.nhi.client import NHI_DOWNLOAD_PATH, NHI_METADATA_PATH
from taiwan_fda_mcp.sources.nhi.dataset import (
    parse_rows,
    payload_sha256,
    read_meta,
    write_meta,
    write_to_cache,
)
from taiwan_fda_mcp.sources.nhi.store import get_nhi_store
from taiwan_fda_mcp.tools import get_nhi_drug_item, list_nhi_drug_items

_BASE = "https://info.nhi.gov.tw"
_PROBE_URL = f"{_BASE}{NHI_METADATA_PATH}"
_DOWNLOAD_URL = f"{_BASE}{NHI_DOWNLOAD_PATH}"
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "nhi_drug_items_sample.csv"
_CACHE_FILE = "nhi_items.json"
_MODIFIED = "2026-08-10T15:25:14"
_RESOURCE_MODIFIED = "2026-07-28 07:01:52"
_CODE = "AC49322100"  # in the fixture, price 8.60
_LICENCE = "衛署藥製字第049322號"  # two items in the fixture

# One probe call = the first attempt plus `max_retries=1` retry.
_PROBE_ATTEMPTS_PER_CALL = 2


def _reset(request: httpx.Request) -> httpx.Response:
    """The failure seen from GitHub runners: TCP and TLS succeed, then a reset."""
    raise httpx.ReadError("Connection reset by peer", request=request)


def _meta_json(modified: str = _MODIFIED) -> dict:
    return {
        "modified": modified,
        "numberOfData": "224553",
        "distribution": [{"resourceModified": _RESOURCE_MODIFIED}],
    }


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "NHI_BASE_URL": _BASE,
        "NHI_CACHE_DIR": tmp_path,
        "NHI_TTL_HOURS": 24,
        "NHI_PROBE_TIMEOUT_SECONDS": 5.0,
        "FDA_RATE_LIMIT_INTERVAL_SECONDS": 0.0,
        "INSERT_THROTTLE_MIN_INTERVAL_SECONDS": 0.0,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _seed_cache(cache_dir: Path, *, age_hours: float) -> None:
    """Write a cache and sidecar as a past download would, backdated by `age_hours`."""
    raw = _FIXTURE.read_bytes()
    rows = parse_rows(raw.decode("utf-8-sig"))
    write_to_cache(rows, cache_dir)
    write_meta(
        NhiCacheMeta(
            payload_sha256=payload_sha256(raw),
            content_length=len(raw),
            row_count=len(rows),
            modified=_MODIFIED,
            resource_modified=_RESOURCE_MODIFIED,
            downloaded_at="2026-08-27T03:00:00+00:00",
        ),
        cache_dir,
    )
    past = time.time() - age_hours * 3600
    os.utime(cache_dir / _CACHE_FILE, (past, past))


@pytest.fixture(autouse=True)
def _clean_store():
    get_nhi_store().reset()
    yield
    get_nhi_store().reset()


@pytest.fixture
def retry_sleeps(monkeypatch) -> list[float]:
    """Record the probe's retry backoff instead of really sleeping it."""
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(nhi_client.asyncio, "sleep", _fake_sleep)
    return sleeps


async def _drain_background_reload() -> None:
    """Let a scheduled background reload finish, so no task outlives the test."""
    task = get_nhi_store()._refresh_task
    if task is not None:
        await task


# --- Scenario 1: first run, no cache on disk -------------------------------


@respx.mock
async def test_first_run_outage_returns_an_error_and_no_data(tmp_path, retry_sleeps):
    """No cache and no network: the tool answers with an error, never with data."""
    respx.get(_PROBE_URL).mock(side_effect=_reset)
    respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)

    r = await get_nhi_drug_item(_CODE, settings=_settings(tmp_path))

    assert r.error is not None
    assert r.error.code == "DATASET_FETCH_FAILED"
    assert r.item is None
    assert r.source_url is None
    assert r.dataset_retrieved_at is None


@respx.mock
async def test_first_run_outage_list_reports_nhi_listed_false_beside_the_error(
    tmp_path, retry_sleeps
):
    """RISK, pinned as-is: `nhi_listed` is False next to the error.

    The field's own description calls False a FACT (此藥未納入健保給付). An LLM
    that reads the field before the error could tell a user "not covered by
    NHI" when the truth is "NHI could not be reached". The server instructions
    say to report errors verbatim, which is the only guard today.
    """
    respx.get(_PROBE_URL).mock(side_effect=_reset)
    respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)

    r = await list_nhi_drug_items(_LICENCE, settings=_settings(tmp_path))

    assert r.error is not None
    assert r.error.code == "DATASET_FETCH_FAILED"
    assert r.nhi_listed is False
    assert r.items == []
    assert r.total == 0


@respx.mock
async def test_first_run_outage_retries_the_whole_cold_load_on_every_query(tmp_path, retry_sleeps):
    """No failure memo: each query repeats the probe AND the 92 MB download.

    In production each query can block for the probe (2 x 5 s + 0.5 s) plus the
    download. The download has a 600 s timeout, so a hang, not a reset, could
    hold a query for up to ten minutes. Queries also queue behind each other on
    the store lock.
    """
    probe = respx.get(_PROBE_URL).mock(side_effect=_reset)
    download = respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)
    s = _settings(tmp_path)

    await get_nhi_drug_item(_CODE, settings=s)
    await get_nhi_drug_item(_CODE, settings=s)

    assert probe.call_count == 2 * _PROBE_ATTEMPTS_PER_CALL
    assert download.call_count == 2  # noqa: PLR2004
    assert not (tmp_path / _CACHE_FILE).exists()  # nothing half-written


# --- Scenario 2: a cache exists but is past its 24 h TTL -------------------


@respx.mock
async def test_stale_cache_outage_serves_last_good_data_marked_stale(tmp_path, retry_sleeps):
    """The user still gets an answer, flagged as stale, with no error."""
    _seed_cache(tmp_path, age_hours=48)
    respx.get(_PROBE_URL).mock(side_effect=_reset)
    download = respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)
    s = _settings(tmp_path)

    item = await get_nhi_drug_item(_CODE, settings=s)
    await _drain_background_reload()
    listed = await list_nhi_drug_items(_LICENCE, settings=s)
    await _drain_background_reload()

    assert item.error is None
    assert item.item_on_file is True
    assert item.item is not None
    assert item.item.price == 8.6  # noqa: PLR2004
    assert item.is_stale is True
    assert item.dataset_age_hours is not None
    assert item.dataset_age_hours >= 48  # noqa: PLR2004
    assert listed.error is None
    assert listed.nhi_listed is True
    assert listed.is_stale is True
    assert download.call_count == 0  # an unconfirmed probe never downloads


@respx.mock
async def test_stale_cache_outage_reprobes_and_pays_the_retry_on_every_query(
    tmp_path, retry_sleeps
):
    """Serving stale data does not stop the probing: every query probes again.

    Each query runs one foreground probe (which the query waits for) and
    schedules one background probe. In production the foreground part costs up
    to 2 x 5 s + 0.5 s = 10.5 s per query for as long as the outage lasts.
    """
    _seed_cache(tmp_path, age_hours=48)
    probe = respx.get(_PROBE_URL).mock(side_effect=_reset)
    s = _settings(tmp_path)

    queries = 3
    for _ in range(queries):
        r = await get_nhi_drug_item(_CODE, settings=s)
        assert r.error is None
        await _drain_background_reload()

    # Foreground + background probe per query, each with its own retry.
    assert probe.call_count == queries * 2 * _PROBE_ATTEMPTS_PER_CALL
    assert retry_sleeps == [0.5] * (queries * 2)


@respx.mock
async def test_concurrent_stale_queries_wait_for_each_others_probe(tmp_path, retry_sleeps):
    """Queries queue on the store lock, so one slow probe delays every caller.

    Three queries arrive together. While the first probe hangs, the other two
    have not reached the network at all: they are waiting for the lock. On the
    shared HTTP service that means N clinicians wait N probes in a row.
    """
    _seed_cache(tmp_path, age_hours=48)
    gate = asyncio.Event()
    first_started = asyncio.Event()
    started = 0

    async def _slow_reset(request: httpx.Request) -> httpx.Response:
        nonlocal started
        started += 1
        first_started.set()
        await gate.wait()
        raise httpx.ReadError("Connection reset by peer", request=request)

    respx.get(_PROBE_URL).mock(side_effect=_slow_reset)
    s = _settings(tmp_path)

    tasks = [asyncio.create_task(get_nhi_drug_item(_CODE, settings=s)) for _ in range(3)]
    await asyncio.wait_for(first_started.wait(), timeout=2)
    for _ in range(50):  # give the other two every chance to reach the network
        await asyncio.sleep(0)
    assert started == 1  # only the lock holder is on the network
    gate.set()
    results = await asyncio.gather(*tasks)
    await _drain_background_reload()

    assert all(r.error is None and r.is_stale for r in results)
    assert started >= 3 * _PROBE_ATTEMPTS_PER_CALL  # each query probed, one after another


# --- Scenario 3: NHI answers the probe but the download fails --------------


@respx.mock
async def test_background_download_outage_keeps_the_last_good_snapshot(tmp_path, retry_sleeps):
    """A failed 92 MB refresh changes nothing: same rows, same files on disk."""
    _seed_cache(tmp_path, age_hours=48)
    respx.get(_PROBE_URL).mock(
        return_value=httpx.Response(200, json=_meta_json("2026-09-20T09:00:00"))
    )
    download = respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)
    s = _settings(tmp_path)
    cache_before = (tmp_path / _CACHE_FILE).read_bytes()
    meta_before = read_meta(tmp_path)

    first = await get_nhi_drug_item(_CODE, settings=s)
    await _drain_background_reload()
    # The in-memory snapshot itself must survive, not just the disk copy. A
    # wiped memo would still look fine to the user, because the next query
    # re-reads the disk, which hides the bug from every tool-level check.
    store = get_nhi_store()
    assert store._rows is not None
    assert _CODE in store._by_code
    second = await get_nhi_drug_item(_CODE, settings=s)
    await _drain_background_reload()

    assert download.call_count >= 1
    assert first.item is not None
    assert second.item is not None
    assert second.item.price == 8.6  # noqa: PLR2004
    assert second.is_stale is True  # still past TTL, and nothing claims otherwise
    assert (tmp_path / _CACHE_FILE).read_bytes() == cache_before
    assert read_meta(tmp_path) == meta_before


# --- Scenario 4: the outage ends --------------------------------------------


@respx.mock
async def test_service_recovers_on_the_first_query_after_the_outage(tmp_path, retry_sleeps):
    """No restart is needed: the next query after recovery is fresh again."""
    _seed_cache(tmp_path, age_hours=48)
    probe = respx.get(_PROBE_URL).mock(side_effect=_reset)
    s = _settings(tmp_path)

    during = await get_nhi_drug_item(_CODE, settings=s)
    await _drain_background_reload()
    probe.mock(side_effect=None, return_value=httpx.Response(200, json=_meta_json()))
    after = await get_nhi_drug_item(_CODE, settings=s)

    assert during.is_stale is True
    assert after.error is None
    assert after.is_stale is False


# --- Scenario 5: the cache is still inside its TTL --------------------------


@respx.mock
async def test_fresh_cache_is_unaffected_by_an_outage(tmp_path, retry_sleeps):
    """Within 24 h of the last load, an outage is invisible: no network at all."""
    _seed_cache(tmp_path, age_hours=1)
    probe = respx.get(_PROBE_URL).mock(side_effect=_reset)
    download = respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)

    r = await get_nhi_drug_item(_CODE, settings=_settings(tmp_path))

    assert r.error is None
    assert r.is_stale is False
    assert probe.call_count == 0
    assert download.call_count == 0


# --- Scenario 6: the shared HTTP service starts during an outage ------------


@respx.mock
async def test_http_startup_during_outage_starts_and_leaves_nhi_empty(
    tmp_path, monkeypatch, retry_sleeps
):
    """Startup does not fail. The first NHI query then pays the cold-load cost."""

    async def _noop(*_args: object, **_kwargs: object) -> list:
        return []

    monkeypatch.setattr(srv, "_load_or_refresh_licenses", _noop)
    monkeypatch.setattr(srv, "get_appearance_store", lambda: SimpleNamespace(get_index=_noop))
    respx.get(_PROBE_URL).mock(side_effect=_reset)
    download = respx.get(_DOWNLOAD_URL).mock(side_effect=_reset)
    s = _settings(tmp_path, MCP_TRANSPORT="http")

    await srv._prewarm_stores(s)  # must not raise
    assert download.call_count == 1
    assert get_nhi_store()._rows is None

    r = await get_nhi_drug_item(_CODE, settings=s)
    assert r.error is not None
    assert download.call_count == 2  # the first real query tried again  # noqa: PLR2004
