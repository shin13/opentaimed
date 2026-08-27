# path: tests/unit/test_nhi_store.py
# brief: Two-tier (probe / payload) refresh policy for the NHI item store.

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.models import NhiCacheMeta
from taiwan_fda_mcp.sources.nhi.client import NHI_DOWNLOAD_PATH, NHI_METADATA_PATH
from taiwan_fda_mcp.sources.nhi.dataset import (
    parse_rows,
    payload_sha256,
    read_meta,
    write_meta,
    write_to_cache,
)
from taiwan_fda_mcp.sources.nhi.store import NhiItemStore

_BASE = "https://info.nhi.gov.tw"
_PROBE_URL = f"{_BASE}{NHI_METADATA_PATH}"
_DOWNLOAD_URL = f"{_BASE}{NHI_DOWNLOAD_PATH}"
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "nhi_drug_items_sample.csv"

# Written by NhiItemStore._persist via sources.nhi.dataset.write_to_cache.
_CACHE_FILE = "nhi_items.json"

_OLD_MODIFIED = "2026-08-10T15:25:14"
_OLD_RESOURCE_MODIFIED = "2026-07-28 07:01:52"
_NEW_MODIFIED = "2026-09-10T09:00:00"
_NEW_RESOURCE_MODIFIED = "2026-09-09 08:00:00"
_SEEDED_DOWNLOADED_AT = "2026-08-27T03:00:00+00:00"


def _settings(tmp_path: Path, ttl_hours: int) -> Settings:
    return Settings(  # type: ignore[call-arg]
        NHI_BASE_URL=_BASE,
        NHI_CACHE_DIR=tmp_path,
        NHI_TTL_HOURS=ttl_hours,
        NHI_PROBE_TIMEOUT_SECONDS=5.0,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
        INSERT_THROTTLE_MIN_INTERVAL_SECONDS=0.0,
    )


def _fresh_settings(tmp_path: Path) -> Settings:
    return _settings(tmp_path, 24)


def _stale_settings(tmp_path: Path) -> Settings:
    """Settings under which every memo is stale.

    A 0-hour TTL suffices because `_is_stale` compares the memo's age with `>=`,
    so a memo loaded this very instant already counts as past its TTL.
    """
    return _settings(tmp_path, 0)


def _meta_json(
    modified: str = _OLD_MODIFIED,
    resource_modified: str = _OLD_RESOURCE_MODIFIED,
    rows: int = 224553,
) -> dict:
    """The 2 KB probe response, shaped as the live endpoint returns it."""
    return {
        "identifier": "A21030000I-E41001",
        "title": "健保用藥品項查詢項目檔",
        "accrualPeriodicity": "每月",
        "modified": modified,
        "numberOfData": str(rows),
        "distribution": [
            {
                "resourceID": "A21030000I-E41001-001",
                "format": "CSV",
                "resourceModified": resource_modified,
            }
        ],
    }


def _payload() -> bytes:
    return _FIXTURE.read_bytes()


def _payload_v2() -> bytes:
    """The fixture with AC49322100's 支付價 changed 8.60 to 7.10 — a new hash."""
    text = _FIXTURE.read_text(encoding="utf-8")
    assert text.count(",8.60,") == 1, "fixture changed; pick another unique price"
    return text.replace(",8.60,", ",7.10,").encode("utf-8")


def _csv_response(body: bytes) -> httpx.Response:
    """A payload response carrying the Content-Length the transfer gate checks."""
    return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})


def _seed(
    cache_dir: Path,
    *,
    body: bytes | None = None,
    modified: str = _OLD_MODIFIED,
    resource_modified: str = _OLD_RESOURCE_MODIFIED,
) -> None:
    """Write an item cache plus a matching sidecar, as a real download would."""
    raw = _payload() if body is None else body
    rows = parse_rows(raw.decode("utf-8-sig"))
    write_to_cache(rows, cache_dir)
    write_meta(
        NhiCacheMeta(
            payload_sha256=payload_sha256(raw),
            content_length=len(raw),
            row_count=len(rows),
            modified=modified,
            resource_modified=resource_modified,
            downloaded_at=_SEEDED_DOWNLOADED_AT,
        ),
        cache_dir,
    )


def _age_cache(cache_dir: Path, hours: float) -> None:
    """Backdate the cache file so `_cold_load` reads the memo as `hours` old.

    Used instead of assigning `store._loaded_at`: the age genuinely comes from
    the file the store reads, so the test exercises the real freshness path.
    """
    past = time.time() - hours * 3600
    os.utime(cache_dir / _CACHE_FILE, (past, past))


async def _await_scheduled_reload(store: NhiItemStore) -> None:
    """Run the store's scheduled background reload to completion.

    The reload is deliberately fire-and-forget, so there is no public handle on
    it. Reading the task — never mutating it — keeps these tests deterministic
    instead of sleeping for a guessed interval.
    """
    task = store._refresh_task
    assert task is not None, "expected get_indexes to schedule a background reload"
    await task


@pytest.mark.asyncio
@respx.mock
async def test_fresh_memo_neither_probes_nor_downloads(tmp_path):
    _seed(tmp_path)
    probe = respx.get(_PROBE_URL).mock(return_value=httpx.Response(200, json=_meta_json()))
    download = respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))

    store = NhiItemStore()
    fresh = _fresh_settings(tmp_path)
    by_code, by_licence = await store.get_indexes(fresh)
    await store.get_indexes(fresh)

    assert probe.call_count == 0
    assert download.call_count == 0
    assert len(by_code) == 7  # noqa: PLR2004
    assert sorted(r.nhi_code for r in by_licence["衛署藥製字第049322號"]) == [
        "A049322100",
        "AC49322100",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_unchanged_timestamps_serve_as_fresh_without_downloading(tmp_path):
    """The whole point of the cheap probe: confirm, then skip the 92 MB fetch."""
    _seed(tmp_path)
    probe = respx.get(_PROBE_URL).mock(return_value=httpx.Response(200, json=_meta_json()))
    download = respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))

    store = NhiItemStore()
    s = _fresh_settings(tmp_path)
    _age_cache(tmp_path, 48)  # older than the 24 h TTL
    by_code, _ = await store.get_indexes(s)

    assert probe.call_count == 1
    assert download.call_count == 0
    assert len(by_code) == 7  # noqa: PLR2004
    _, _, is_stale = store.freshness(s)
    assert is_stale is False  # honestly fresh: upstream was asked and said nothing moved


@pytest.mark.asyncio
@respx.mock
async def test_moved_timestamp_serves_stale_immediately_then_swaps(tmp_path):
    _seed(tmp_path)
    _age_cache(tmp_path, 48)
    respx.get(_PROBE_URL).mock(
        return_value=httpx.Response(
            200, json=_meta_json(_NEW_MODIFIED, _NEW_RESOURCE_MODIFIED)
        )
    )
    download = respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload_v2()))

    store = NhiItemStore()
    by_code, _ = await store.get_indexes(_fresh_settings(tmp_path))

    # Served from the snapshot: the 92 MB download has not been issued yet.
    assert download.call_count == 0
    assert by_code["AC49322100"].price == 8.6  # noqa: PLR2004

    await _await_scheduled_reload(store)
    assert download.call_count == 1

    swapped, _ = await store.get_indexes(_fresh_settings(tmp_path))
    assert swapped["AC49322100"].price == 7.1  # noqa: PLR2004


@pytest.mark.asyncio
@respx.mock
async def test_identical_payload_hash_advances_timestamps_without_touching_rows(tmp_path):
    """The payload hash is the version identity — a moved timestamp is only a hint."""
    _seed(tmp_path)
    _age_cache(tmp_path, 48)
    respx.get(_PROBE_URL).mock(
        return_value=httpx.Response(
            200, json=_meta_json(_NEW_MODIFIED, _NEW_RESOURCE_MODIFIED)
        )
    )
    respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))  # same bytes

    store = NhiItemStore()
    await store.get_indexes(_fresh_settings(tmp_path))
    await _await_scheduled_reload(store)

    meta = read_meta(tmp_path)
    assert meta is not None
    assert meta.payload_sha256 == payload_sha256(_payload())  # unchanged
    assert meta.modified == _NEW_MODIFIED  # advanced, so the next cycle stays quiet
    assert meta.resource_modified == _NEW_RESOURCE_MODIFIED
    # `downloaded_at` is what proves the short-circuit ran rather than a full
    # re-persist: the rows and the hash are identical either way, so asserting
    # on them cannot tell the two apart. Mutation testing found exactly that —
    # removing the `payload_sha256 == digest` branch left every other assertion
    # in this test green. The short-circuit preserves the seeded timestamp;
    # _persist would overwrite it with datetime.now(UTC).
    assert meta.downloaded_at == _SEEDED_DOWNLOADED_AT
    by_code, _ = await store.get_indexes(_fresh_settings(tmp_path))
    assert by_code["AC49322100"].price == 8.6  # noqa: PLR2004


@pytest.mark.asyncio
@respx.mock
async def test_probe_failure_serves_stale_and_does_not_download(tmp_path):
    _seed(tmp_path)
    _age_cache(tmp_path, 48)
    respx.get(_PROBE_URL).mock(return_value=httpx.Response(500))
    download = respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))

    store = NhiItemStore()
    s = _fresh_settings(tmp_path)
    by_code, _ = await store.get_indexes(s)

    assert len(by_code) == 7  # last-good snapshot, not an error  # noqa: PLR2004
    _, _, is_stale = store.freshness(s)
    assert is_stale is True
    await _await_scheduled_reload(store)  # the retry probes again and also fails
    assert download.call_count == 0  # never downloads on an unconfirmed probe


@pytest.mark.asyncio
@respx.mock
async def test_two_stale_queries_schedule_one_download(tmp_path):
    """The single-in-flight guard, made deterministic by a download that hangs."""
    _seed(tmp_path)
    _age_cache(tmp_path, 48)
    respx.get(_PROBE_URL).mock(
        return_value=httpx.Response(
            200, json=_meta_json(_NEW_MODIFIED, _NEW_RESOURCE_MODIFIED)
        )
    )
    release = asyncio.Event()
    calls = 0

    async def _hang(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await release.wait()
        return _csv_response(_payload_v2())

    respx.get(_DOWNLOAD_URL).mock(side_effect=_hang)

    store = NhiItemStore()
    s = _fresh_settings(tmp_path)
    await store.get_indexes(s)  # schedules reload #1, which blocks in the download
    await store.get_indexes(s)  # must reuse the in-flight task, not start a second

    release.set()
    await _await_scheduled_reload(store)
    assert calls == 1


@pytest.mark.asyncio
@respx.mock
async def test_cold_start_probes_before_downloading_and_records_the_timestamps(tmp_path):
    """Regression guard: an empty sidecar would re-download 92 MB every 24 h."""
    probe = respx.get(_PROBE_URL).mock(return_value=httpx.Response(200, json=_meta_json()))
    download = respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))

    store = NhiItemStore()
    by_code, _ = await store.get_indexes(_fresh_settings(tmp_path))

    assert len(by_code) == 7  # noqa: PLR2004
    assert probe.call_count == 1
    assert download.call_count == 1
    meta = read_meta(tmp_path)
    assert meta is not None
    assert meta.modified == _OLD_MODIFIED
    assert meta.resource_modified == _OLD_RESOURCE_MODIFIED


@pytest.mark.asyncio
@respx.mock
async def test_cold_start_survives_a_failing_probe(tmp_path):
    """A probe failure must not stop a first run from serving data."""
    respx.get(_PROBE_URL).mock(return_value=httpx.Response(500))
    respx.get(_DOWNLOAD_URL).mock(return_value=_csv_response(_payload()))

    store = NhiItemStore()
    by_code, _ = await store.get_indexes(_fresh_settings(tmp_path))

    assert len(by_code) == 7  # noqa: PLR2004
    meta = read_meta(tmp_path)
    assert meta is not None
    assert meta.modified == ""  # honest: the timestamps were never observed


@pytest.mark.asyncio
@respx.mock
async def test_cold_start_download_failure_raises(tmp_path):
    respx.get(_PROBE_URL).mock(return_value=httpx.Response(200, json=_meta_json()))
    respx.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(500))

    store = NhiItemStore()
    with pytest.raises(DatasetFetchError):
        await store.get_indexes(_fresh_settings(tmp_path))


@pytest.mark.asyncio
@respx.mock
async def test_shutdown_cancels_an_in_flight_reload_and_is_idempotent(tmp_path):
    _seed(tmp_path)
    _age_cache(tmp_path, 48)
    respx.get(_PROBE_URL).mock(
        return_value=httpx.Response(
            200, json=_meta_json(_NEW_MODIFIED, _NEW_RESOURCE_MODIFIED)
        )
    )
    started = asyncio.Event()

    async def _hang(_request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()  # never resolves; only cancellation ends it
        raise AssertionError("unreachable")

    respx.get(_DOWNLOAD_URL).mock(side_effect=_hang)

    store = NhiItemStore()
    await store.get_indexes(_fresh_settings(tmp_path))
    await asyncio.wait_for(started.wait(), timeout=2)

    await store.shutdown()
    await store.shutdown()  # idempotent
    await NhiItemStore().shutdown()  # also safe on a store that never refreshed
