# path: tests/unit/test_tools.py
# brief: Verify tools.py entry-point behaviour.

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

import taiwan_fda_mcp.tools as _tools_mod
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows, write_to_cache
from taiwan_fda_mcp.tools import (
    check_insert_updates,
    get_package_insert,
    search_drugs,
)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Clear tools.py module-level Dataset 37 memo between tests."""
    _tools_mod._LICENSES_CACHE = None


@pytest.fixture
def seeded_settings(tmp_path: Path, fixtures_dir: Path) -> Settings:
    """Settings with a pre-seeded Dataset 37 cache so tools never hit the network for it."""
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    cache_dir = tmp_path / "ds37"
    write_to_cache(rows, cache_dir)
    return Settings(  # type: ignore[call-arg]
        DATASET37_CACHE_DIR=cache_dir,
        DATASET37_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )


@pytest.mark.asyncio
async def test_search_drugs_returns_dicts(seeded_settings):
    results = await search_drugs(query="脈優", settings=seeded_settings)
    assert len(results) == 1
    row = results[0]
    assert row["license_no"] == "衛署藥輸字第021571號"
    assert row["name_zh"] == "脈優錠５毫克"


@pytest.mark.asyncio
async def test_get_package_insert_key_fields(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = await get_package_insert(
            license_no="衛署藥輸字第021571號",
            settings=seeded_settings,
        )

    assert result["license_no"] == "衛署藥輸字第021571號"
    fields = result["fields"]
    assert "indication" in fields
    assert "高血壓" in fields["indication"]
    assert "dosage" in fields
    assert "5 mg" in fields["dosage"]
    assert "warnings" in fields
    assert "心衰竭" in fields["warnings"]
    assert "side_effects" in fields
    assert "頭痛" in fields["side_effects"]

    assert result["source_url"].endswith("license=02021571&s_code=&startdate=&enddate=")
    assert result["last_update_date"] == "2025-10-29"
    datetime.fromisoformat(result["retrieved_at"])


@pytest.mark.asyncio
async def test_get_package_insert_explicit_fields(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = await get_package_insert(
            license_no="衛署藥輸字第021571號",
            fields=["indication", "name_zh", "manufacturer"],
            settings=seeded_settings,
        )
    assert set(result["fields"]) == {"indication", "name_zh", "manufacturer"}
    assert result["fields"]["name_zh"] == "脈優錠５毫克"
    assert result["fields"]["manufacturer"] == "久裕企業股份有限公司"


@pytest.mark.asyncio
async def test_get_package_insert_unsupported_prefix(seeded_settings):
    result = await get_package_insert(
        license_no="衛部中藥製字第000001號",
        settings=seeded_settings,
    )
    assert result["error"]["code"] == "LICENSE_PREFIX_UNSUPPORTED"


@pytest.mark.asyncio
async def test_check_insert_updates_batches_date_ranges(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        results = await check_insert_updates(
            since_date="2025-10-01",
            today="2025-10-29",
            settings=seeded_settings,
        )
    # 29-day span → 3 batches of ≤10 days
    assert route.call_count == 3  # noqa: PLR2004
    assert any(r["license_no"] == "衛署藥輸字第021571號" for r in results)
    assert all(r["has_updated"] for r in results)


@pytest.mark.asyncio
async def test_check_insert_updates_filters_license_list(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        results = await check_insert_updates(
            since_date="2025-10-25",
            today="2025-10-29",
            license_list=["衛部藥輸字第026701號"],
            settings=seeded_settings,
        )
    assert results == []
