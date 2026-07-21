# path: tests/unit/test_mcp_server.py
# brief: Verify FastMCP server exposes the three tools and routes calls.

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastmcp import Client

import taiwan_fda_mcp.mcp_server as srv
import taiwan_fda_mcp.tools as tools_mod
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.mcp_server import get_drug_appearance, mcp
from taiwan_fda_mcp.sources.opendata.appearance_store import get_appearance_store
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows, write_to_cache
from taiwan_fda_mcp.sources.opendata.dataset42 import parse_rows as parse_rows_42
from taiwan_fda_mcp.sources.opendata.dataset42 import write_to_cache as write_to_cache_42


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


def test_main_defaults_to_stdio(monkeypatch):
    """No env → mcp.run() with no transport kwarg (Model A regression guard)."""
    calls: list[tuple] = []
    monkeypatch.setattr(srv.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(srv, "get_settings", lambda: Settings(MCP_TRANSPORT="stdio"))  # type: ignore[call-arg]
    srv.main()
    assert calls == [((), {})]


def test_main_http_passes_transport_host_port_path(monkeypatch):
    """MCP_TRANSPORT=http → mcp.run(transport='http', host, port, path)."""
    http_host = "0.0.0.0"  # noqa: S104 — intended inside a container
    calls: list[tuple] = []
    monkeypatch.setattr(srv.mcp, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(
        srv,
        "get_settings",
        lambda: Settings(  # type: ignore[call-arg]
            MCP_TRANSPORT="http",
            MCP_HTTP_HOST=http_host,
            MCP_HTTP_PORT=9000,
            MCP_HTTP_PATH="/mcp/",
        ),
    )
    srv.main()
    assert calls == [
        ((), {"transport": "http", "host": http_host, "port": 9000, "path": "/mcp/"})
    ]


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok():
    """The /health route answers 200 OK over the HTTP app (proxy readiness probe)."""
    app = mcp.http_app()  # Starlette ASGI app incl. custom routes
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200  # noqa: PLR2004
    assert resp.text == "OK"


@pytest.mark.asyncio
async def test_lists_all_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "search_drugs",
        "search_by_ingredient",
        "get_package_insert",
        "check_insert_updates",
        "get_drug_appearance",
    }


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


@pytest.mark.asyncio
async def test_get_package_insert_accepts_json_string_fields(fixtures_dir: Path):
    """`fields` sent as a JSON-array STRING (Claude Desktop stringify quirk) is honored."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        async with Client(mcp) as client:
            result = await client.call_tool(
                "get_package_insert",
                {"license_no": "衛署藥輸字第021571號", "fields": '["indication"]'},
            )
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert "indication" in payload["fields"]


@pytest.mark.asyncio
async def test_check_insert_updates_accepts_json_string_license_list(fixtures_dir: Path):
    """`license_list` sent as a JSON-array STRING is coerced, not rejected."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        async with Client(mcp) as client:
            result = await client.call_tool(
                "check_insert_updates",
                {"since_date": "2026-07-18", "license_list": '["衛署藥輸字第021571號"]'},
            )
    payload = result.structured_content or json.loads(result.content[0].text)  # type: ignore[union-attr]
    assert payload["error"] is None
    assert "total" in payload


def _resource_text(content) -> str:
    """Extract the text body from a FastMCP read_resource result."""
    return content[0].text if isinstance(content, list) else content.contents[0].text


@pytest.mark.asyncio
async def test_rx_structure_resource_listed_and_readable():
    """The Rx-insert-structure resource is listed and readable via the MCP server."""
    async with Client(mcp) as client:
        resources = await client.list_resources()
        uris = [str(r.uri) for r in resources]
        assert "structure://rx-insert" in uris

        text = _resource_text(await client.read_resource("structure://rx-insert"))
        assert "處方藥" in text
        assert "1.2 賦形劑" in text
        assert "6.5 老年人" in text
        assert "10.3 臨床前安全性資料" in text
        assert "衛福部 110.09.14" in text  # source citation
        # field-name map present
        assert "special_warning" in text
        assert "geriatric" in text


@pytest.mark.asyncio
async def test_otc_structure_resource_listed_and_readable():
    """The OTC-insert-structure resource is listed and readable."""
    async with Client(mcp) as client:
        resources = await client.list_resources()
        uris = [str(r.uri) for r in resources]
        assert "structure://otc-insert" in uris

        text = _resource_text(await client.read_resource("structure://otc-insert"))
        assert "非處方藥" in text
        assert "【成分】" in text
        assert "【用法用量】" in text
        # real OTC field-name map (no longer a placeholder)
        assert "usage" in text
        assert "usage_precautions" in text
        assert "otc_warnings" in text


@pytest.mark.asyncio
async def test_get_drug_appearance_tool_registered(tmp_path, monkeypatch):
    get_appearance_store().reset()
    write_to_cache_42(
        parse_rows_42([{"許可證字號": "L1", "中文品名": "藥", "形狀": "圓形"}]), tmp_path
    )
    overridden = Settings(  # type: ignore[call-arg]
        DATASET42_CACHE_DIR=tmp_path,
        DATASET42_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )
    monkeypatch.setattr(tools_mod, "get_settings", lambda: overridden)

    resp = await get_drug_appearance("L1")
    assert resp.appearance_on_file is True
    assert resp.shape == "圓形"
