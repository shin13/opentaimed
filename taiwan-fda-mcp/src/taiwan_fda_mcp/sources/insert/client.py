# path: src/taiwan_fda_mcp/sources/insert/client.py
# brief: Async GetDrugDoc client (mcp.fda.gov.tw) with simple rate limit.

import asyncio
import logging

import httpx

from taiwan_fda_mcp.exceptions import InsertFetchError, RCode
from taiwan_fda_mcp.models import DrugInsert
from taiwan_fda_mcp.sources.insert.parser import parse_get_drug_doc

_logger = logging.getLogger(__name__)

_PATH = "/Serv/Query.asmx/GetDrugDoc"


async def fetch_drug_insert(
    *,
    base_url: str,
    license_code: str | None = None,
    s_code: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    rate_limit_interval: float = 0.5,
    timeout: float = 30.0,  # noqa: ASYNC109
) -> list[DrugInsert]:
    """Fetch inserts from mcp.fda.gov.tw GetDrugDoc.

    At least one of license_code / s_code / startdate / enddate must be supplied.
    Date range is capped by the API at 10 days — caller is responsible for batching.

    Args:
        base_url: e.g. 'https://mcp.fda.gov.tw'
        license_code: 8-digit code from license_str_to_code.
        s_code: 健保代碼 (rarely used in MVP).
        startdate / enddate: 'YYYY/MM/DD' bounds (inclusive, ≤ 10 days apart).
        rate_limit_interval: seconds to sleep AFTER the request.
        timeout: per-request timeout.

    Raises:
        InsertFetchError: HTTP failure or missing required params.
        InsertParseError: XML parse failure / API <Error> response.
    """
    if not any([license_code, s_code, startdate, enddate]):
        raise InsertFetchError(
            RCode.INSERT_FETCH_FAILED,
            "GetDrugDoc requires at least one of license_code, s_code, startdate, enddate",
        )

    params: dict[str, str] = {}
    if license_code:
        params["license"] = license_code
    if s_code:
        params["s_code"] = s_code
    if startdate:
        params["startdate"] = startdate
    if enddate:
        params["enddate"] = enddate

    url = f"{base_url.rstrip('/')}{_PATH}"
    _logger.info("insert.fetch.start", extra={"params": params})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            body = response.content
    except httpx.HTTPError as exc:
        raise InsertFetchError(
            RCode.INSERT_FETCH_FAILED,
            f"GetDrugDoc HTTP failure: {exc}",
            detail={"params": params},
        ) from exc
    finally:
        if rate_limit_interval > 0:
            await asyncio.sleep(rate_limit_interval)

    return parse_get_drug_doc(body)
