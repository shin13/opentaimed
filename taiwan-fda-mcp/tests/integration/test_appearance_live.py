# path: tests/integration/test_appearance_live.py
# brief: Live Dataset 42 contract check (network; run on demand).

import pytest

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.sources.opendata.appearance_store import AppearanceStore


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataset42_live_contract(tmp_path):
    """Live: Dataset 42 downloads and a known license resolves with descriptors."""
    settings = Settings(  # type: ignore[call-arg]
        DATASET42_CACHE_DIR=tmp_path,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )
    store = AppearanceStore()
    index = await store.get_index(settings)
    assert len(index) > 1000  # noqa: PLR2004  ~6.2K rows expected
    # spot-check one row has an official image URL on the expected host
    any_url = next((r.image_url for r in index.values() if r.image_url), "")
    assert any_url.startswith("https://mcp.fda.gov.tw/")
