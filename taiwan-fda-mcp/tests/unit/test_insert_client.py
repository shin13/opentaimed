# path: tests/unit/test_insert_client.py
# brief: Verify GetDrugDoc HTTP client behaviour.

from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.exceptions import InsertFetchError, InsertParseError
from taiwan_fda_mcp.sources.insert.client import fetch_drug_insert
from taiwan_fda_mcp.sources.insert.throttle import InsertEgressThrottle


class CountingThrottle(InsertEgressThrottle):
    """A throttle that records how many times acquire() was awaited."""

    def __init__(self) -> None:
        super().__init__(min_interval=0.0)
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1
        await super().acquire()


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
    # With retries disabled, a 503 surfaces immediately as InsertFetchError.
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(503)
        )
        with pytest.raises(InsertFetchError):
            await fetch_drug_insert(
                base_url="https://mcp.fda.gov.tw",
                license_code="02021571",
                rate_limit_interval=0.0,
                max_retries=0,
            )
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_fetch_retries_on_5xx_then_succeeds(sample_xml):
    """Transient 5xx (e.g. the 057312 incident on 2026-05-24) should be retried."""
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, content=sample_xml),
            ]
        )
        inserts = await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            license_code="02021571",
            rate_limit_interval=0.0,
            max_retries=2,
            retry_backoff=0.0,
        )
    assert route.call_count == 2  # noqa: PLR2004 — 1 retry after 1 transient failure
    assert len(inserts) == 1


@pytest.mark.asyncio
async def test_fetch_exhausts_retries_on_persistent_5xx():
    """If 5xx persists across all attempts, raise with attempt count surfaced."""
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(500)
        )
        with pytest.raises(InsertFetchError) as exc_info:
            await fetch_drug_insert(
                base_url="https://mcp.fda.gov.tw",
                license_code="02021571",
                rate_limit_interval=0.0,
                max_retries=2,
                retry_backoff=0.0,
            )
    assert route.call_count == 3  # noqa: PLR2004 — 1 initial + 2 retries
    assert exc_info.value.detail == {
        "params": {"license": "02021571", "s_code": "", "startdate": "", "enddate": ""},
        "attempts": 3,
    }


@pytest.mark.asyncio
async def test_fetch_does_not_retry_on_4xx():
    """4xx is a deterministic client error — retrying wastes time and rate budget."""
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(404)
        )
        with pytest.raises(InsertFetchError):
            await fetch_drug_insert(
                base_url="https://mcp.fda.gov.tw",
                license_code="02021571",
                rate_limit_interval=0.0,
                max_retries=2,
                retry_backoff=0.0,
            )
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_fetch_acquires_throttle_once_on_success(sample_xml):
    """Every successful insert fetch passes through the egress gate exactly once."""
    throttle = CountingThrottle()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=sample_xml)
        )
        await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            license_code="02021571",
            rate_limit_interval=0.0,
            throttle=throttle,
        )
    assert throttle.calls == 1


@pytest.mark.asyncio
async def test_fetch_acquires_throttle_per_attempt_including_retries(sample_xml):
    """Retries are also egress — each HTTP attempt must pass the gate."""
    throttle = CountingThrottle()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, content=sample_xml),
            ]
        )
        await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            license_code="02021571",
            rate_limit_interval=0.0,
            max_retries=2,
            retry_backoff=0.0,
            throttle=throttle,
        )
    assert throttle.calls == 2  # noqa: PLR2004 — initial attempt + 1 retry


@pytest.mark.asyncio
async def test_fetch_retries_on_transport_error(sample_xml):
    """Network blips (ConnectError / ReadTimeout) should be retried, not surfaced."""
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            side_effect=[
                httpx.ConnectError("simulated network blip"),
                httpx.Response(200, content=sample_xml),
            ]
        )
        inserts = await fetch_drug_insert(
            base_url="https://mcp.fda.gov.tw",
            license_code="02021571",
            rate_limit_interval=0.0,
            max_retries=2,
            retry_backoff=0.0,
        )
    assert route.call_count == 2  # noqa: PLR2004 — 1 retry after 1 transient failure
    assert len(inserts) == 1
