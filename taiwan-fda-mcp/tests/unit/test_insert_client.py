# path: tests/unit/test_insert_client.py
# brief: Verify GetDrugDoc HTTP client behaviour.

from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.exceptions import InsertFetchError, InsertParseError
from taiwan_fda_mcp.sources.insert.client import fetch_drug_insert


@pytest.fixture
def sample_xml(fixtures_dir: Path) -> bytes:
    return (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()


@pytest.mark.asyncio
async def test_fetch_by_license_code(sample_xml):
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=sample_xml)
        )
        inserts = await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            license_code="02021571",
            rate_limit_interval=0.0,
        )
    assert route.called
    request = route.calls[0].request
    assert request.url.params["license"] == "02021571"
    # FDA API requires all 4 keys present (values may be blank); missing keys → HTTP 500.
    assert request.url.params["s_code"] == ""
    assert request.url.params["startdate"] == ""
    assert request.url.params["enddate"] == ""
    assert len(inserts) == 1
    assert inserts[0].license_no == "衛署藥輸字第021571號"


@pytest.mark.asyncio
async def test_fetch_by_date_range(sample_xml):
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=sample_xml)
        )
        await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            startdate="2026/05/10",
            enddate="2026/05/17",
            rate_limit_interval=0.0,
        )
    request = route.calls[0].request
    assert request.url.params["startdate"] == "2026/05/10"
    assert request.url.params["enddate"] == "2026/05/17"


@pytest.mark.asyncio
async def test_fetch_requires_at_least_one_param():
    with pytest.raises(InsertFetchError):
        await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            rate_limit_interval=0.0,
        )


@pytest.mark.asyncio
async def test_fetch_propagates_parser_error():
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=b"not xml")
        )
        with pytest.raises(InsertParseError):
            await fetch_drug_insert(
                base_url="https://mcp.fda.gov.tw",
                license_code="02021571",
                rate_limit_interval=0.0,
            )


@pytest.mark.asyncio
async def test_fetch_raises_on_http_error():
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(return_value=httpx.Response(503))
        with pytest.raises(InsertFetchError):
            await fetch_drug_insert(
                base_url="https://mcp.fda.gov.tw",
                license_code="02021571",
                rate_limit_interval=0.0,
            )
