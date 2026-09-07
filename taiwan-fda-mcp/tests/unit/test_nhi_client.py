# path: tests/unit/test_nhi_client.py
# brief: respx-mocked tests for the NHI metadata probe and payload download.

from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.sources.nhi.client import (
    NHI_DOWNLOAD_PATH,
    NHI_METADATA_PATH,
    fetch_drug_items,
    probe_metadata,
)

_BASE = "https://info.nhi.gov.tw"
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "nhi_drug_items_sample.csv"

_META_JSON = {
    "identifier": "A21030000I-E41001",
    "title": "健保用藥品項查詢項目檔",
    "accrualPeriodicity": "每月",
    "modified": "2026-08-10T15:25:14",
    "numberOfData": "224553",
    "distribution": [
        {
            "resourceID": "A21030000I-E41001-001",
            "format": "CSV",
            "resourceModified": "2026-07-28 07:01:52",
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_probe_reads_both_timestamps_and_the_row_count():
    respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(
        return_value=httpx.Response(200, json=_META_JSON)
    )
    meta = await probe_metadata(_BASE)
    assert meta.modified == "2026-08-10T15:25:14"
    assert meta.resource_modified == "2026-07-28 07:01:52"
    assert meta.number_of_data == 224553  # noqa: PLR2004


@pytest.mark.asyncio
@respx.mock
async def test_probe_rejects_the_http_200_not_found_body():
    """/rest/dataset with a resource ID returns HTTP 200 and the string 'Not found'.

    Classed as DATASET_EMPTY, not DATASET_PARSE_FAILED: the host was reachable
    and answered, it just returned no dataset — that is an upstream ID to
    investigate, not a schema this parser must follow.
    """
    respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(
        return_value=httpx.Response(200, text="Not found")
    )
    with pytest.raises(DatasetFetchError) as caught:
        await probe_metadata(_BASE)
    assert caught.value.code is RCode.DATASET_EMPTY


@pytest.mark.asyncio
@respx.mock
async def test_probe_flags_a_404_as_a_moved_endpoint():
    """A 404 is our problem to fix (wrong URL / dataset ID), not a blip."""
    respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(return_value=httpx.Response(404))
    with pytest.raises(DatasetFetchError) as caught:
        await probe_metadata(_BASE, max_retries=2, retry_backoff=0.0)
    assert caught.value.code is RCode.DATASET_HTTP_STATUS


@pytest.mark.asyncio
@respx.mock
async def test_probe_flags_a_missing_field_as_schema_drift():
    """A dataset came back, but a field this parser reads is gone."""
    drifted = {k: v for k, v in _META_JSON.items() if k != "modified"}
    respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(
        return_value=httpx.Response(200, json=drifted)
    )
    with pytest.raises(DatasetFetchError) as caught:
        await probe_metadata(_BASE)
    assert caught.value.code is RCode.DATASET_PARSE_FAILED


@pytest.mark.asyncio
@respx.mock
async def test_probe_wraps_transport_errors_after_retries_exhausted():
    route = respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(DatasetFetchError) as caught:
        await probe_metadata(_BASE, max_retries=2, retry_backoff=0.0)
    assert route.call_count == 3  # noqa: PLR2004 — initial attempt + 2 retries
    assert caught.value.code is RCode.DATASET_FETCH_FAILED


@pytest.mark.asyncio
@respx.mock
async def test_probe_retries_a_transport_blip_then_succeeds():
    """A dropped connection (the observed live failure) must not surface.

    Attempt count is asserted against the DEFAULT max_retries, which is a
    blocking-time contract: this probe runs under the store lock, so raising
    the default raises worst-case query latency (see the client docstring).
    """
    route = respx.get(f"{_BASE}{NHI_METADATA_PATH}").mock(
        side_effect=[
            httpx.ReadError("simulated network blip"),
            httpx.Response(200, json=_META_JSON),
        ]
    )
    meta = await probe_metadata(_BASE, retry_backoff=0.0)
    assert route.call_count == 2  # noqa: PLR2004 — default is exactly 1 retry
    assert meta.number_of_data == 224553  # noqa: PLR2004


@pytest.mark.asyncio
@respx.mock
async def test_fetch_returns_rows_hash_and_byte_count():
    body = _FIXTURE.read_bytes()
    respx.get(f"{_BASE}{NHI_DOWNLOAD_PATH}").mock(
        return_value=httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})
    )
    rows, digest, size = await fetch_drug_items(_BASE)
    assert len(rows) == 7  # noqa: PLR2004
    assert len(digest) == 64  # noqa: PLR2004
    assert size == len(body)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_rejects_a_wrong_body_served_with_200():
    respx.get(f"{_BASE}{NHI_DOWNLOAD_PATH}").mock(
        return_value=httpx.Response(200, text="<html>redirect</html>")
    )
    with pytest.raises(DatasetFetchError):
        await fetch_drug_items(_BASE)
