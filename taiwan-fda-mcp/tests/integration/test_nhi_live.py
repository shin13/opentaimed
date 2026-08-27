# path: tests/integration/test_nhi_live.py
# brief: Live contract check for the NHI metadata probe. Never downloads the 92 MB payload.

import pytest

from taiwan_fda_mcp.sources.nhi.client import probe_metadata

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_live_probe_returns_both_timestamps():
    meta = await probe_metadata("https://info.nhi.gov.tw", timeout=15.0)
    assert meta.modified
    assert meta.resource_modified
    assert meta.number_of_data > 100_000  # noqa: PLR2004  — 224,553 as of 2026-08-27
