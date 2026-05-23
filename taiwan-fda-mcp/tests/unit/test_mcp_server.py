# path: tests/unit/test_mcp_server.py
# brief: Verify FastMCP server exposes the three tools and routes calls.

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastmcp import Client

import taiwan_fda_mcp.tools as tools_mod
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.mcp_server import mcp
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows, write_to_cache


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, tmp_path, fixtures_dir):
    """Seed local Dataset 37 cache + zero rate limit + reset module-level memo."""
    tools_mod._LICENSES_CACHE = None

    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    cache_dir = tmp_path / "ds37"
    write_to_cache(rows, cache_dir)
    overridden = Settings(  # type: ignore[call-arg]
        DATASET37_CACHE_DIR=cache_dir,
        DATASET37_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )
    monkeypatch.setattr(tools_mod, "get_settings", lambda: overridden)


@pytest.mark.asyncio
async def test_lists_three_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_drugs", "get_package_insert", "check_insert_updates"}


@pytest.mark.asyncio
async def test_search_drugs_tool():
    async with Client(mcp) as client:
        result = await client.call_tool("search_drugs", {"query": "atorvastatin"})
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    if isinstance(payload, dict) and "result" in payload and "results" not in payload:
        # FastMCP wraps scalar/list returns in {"result": ...}; dict returns are passed through.
        payload = payload["result"]
    assert payload["total_matched"] == 2  # noqa: PLR2004
    assert payload["returned"] == 2  # noqa: PLR2004
    assert payload["truncated"] is False
    assert payload["error"] is None
    assert len(payload["results"]) == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_get_package_insert_tool(fixtures_dir: Path):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_package_insert",
                {"license_no": "衛署藥輸字第021571號"},
            )
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["license_no"] == "衛署藥輸字第021571號"
    assert "indication" in payload["fields"]
