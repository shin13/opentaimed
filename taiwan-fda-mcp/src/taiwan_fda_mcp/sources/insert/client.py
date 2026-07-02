# path: src/taiwan_fda_mcp/sources/insert/client.py
# brief: Async GetDrugDoc client (mcp.fda.gov.tw) with simple rate limit.

import asyncio
import logging

import httpx

from taiwan_fda_mcp.exceptions import InsertFetchError, RCode
from taiwan_fda_mcp.models import DrugInsert
from taiwan_fda_mcp.sources.insert.parser import parse_get_drug_doc
from taiwan_fda_mcp.sources.insert.throttle import InsertEgressThrottle, get_insert_throttle

_logger = logging.getLogger(__name__)

_PATH = "/Serv/Query.asmx/GetDrugDoc"

# HTTP 5xx range — treated as transient/server-side and retried.
_HTTP_5XX_MIN = 500
_HTTP_6XX_MIN = 600

# mcp.fda.gov.tw blocks the default python-httpx UA with 403. Use a generic browser UA.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


async def fetch_drug_insert_bytes(
    *,
    base_url: str,
    license_code: str | None = None,
    s_code: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    rate_limit_interval: float = 0.5,
    timeout: float = 120.0,  # noqa: ASYNC109 — wide date ranges return 20MB+ XML in 30-60s
    max_retries: int = 2,
    retry_backoff: float = 0.5,
    throttle: InsertEgressThrottle | None = None,
) -> bytes:
    """Fetch the raw GetDrugDoc XML bytes from mcp.fda.gov.tw (no parsing).

    At least one of license_code / s_code / startdate / enddate must be supplied.
    Date range is capped by the API at 10 days — caller is responsible for batching.

    Retries transient failures (5xx responses + transport/timeout errors) with
    exponential backoff: `retry_backoff * 2**attempt` seconds. 4xx responses
    and parse errors are NOT retried — they are deterministic per (params, body).

    Args:
        base_url: e.g. 'https://mcp.fda.gov.tw'
        license_code: 8-digit code from license_str_to_code.
        s_code: 健保代碼 (rarely used in MVP).
        startdate / enddate: 'YYYY/MM/DD' bounds (inclusive, ≤ 10 days apart).
        rate_limit_interval: seconds to sleep AFTER the (final) request.
        timeout: per-request timeout.
        max_retries: number of retry attempts on transient failure (default 2,
            i.e. up to 3 total HTTP calls).
        retry_backoff: base sleep seconds between retries; doubles each attempt.
        throttle: process-wide egress gate; defaults to the shared singleton.
            Each HTTP attempt awaits `throttle.acquire()` before sending.

    Raises:
        InsertFetchError: HTTP failure (after retries exhausted) or missing
            required params.
    """
    if not any([license_code, s_code, startdate, enddate]):
        raise InsertFetchError(
            RCode.INSERT_FETCH_FAILED,
            "GetDrugDoc requires at least one of license_code, s_code, startdate, enddate",
        )

    params: dict[str, str] = {
        "license": license_code or "",
        "s_code": s_code or "",
        "startdate": startdate or "",
        "enddate": enddate or "",
    }

    url = f"{base_url.rstrip('/')}{_PATH}"
    _logger.info("insert.fetch.start", extra={"params": params})

    throttle = throttle or get_insert_throttle()

    body: bytes | None = None
    last_exc: Exception | None = None
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": _USER_AGENT}
        ) as client:
            for attempt in range(max_retries + 1):
                # Shared inter-request rate floor (process-wide), gating every
                # attempt incl. retries. Distinct from the per-call tail-sleep
                # on rate_limit_interval in the `finally` below.
                await throttle.acquire()
                try:
                    response = await client.get(url, params=params)
                except httpx.RequestError as exc:
                    # Transport / timeout / connect failure — no response received.
                    last_exc = exc
                    if attempt < max_retries:
                        sleep_for = retry_backoff * (2**attempt)
                        _logger.warning(
                            "insert.fetch.retry",
                            extra={
                                "attempt": attempt + 1,
                                "reason": "transport_error",
                                "error": str(exc),
                                "sleep_for": sleep_for,
                            },
                        )
                        await asyncio.sleep(sleep_for)
                        continue
                    raise InsertFetchError(
                        RCode.INSERT_FETCH_FAILED,
                        f"GetDrugDoc transport failure after {attempt + 1} attempts: {exc}",
                        detail={"params": params, "attempts": attempt + 1},
                    ) from exc

                if (
                    _HTTP_5XX_MIN <= response.status_code < _HTTP_6XX_MIN
                    and attempt < max_retries
                ):
                    sleep_for = retry_backoff * (2**attempt)
                    _logger.warning(
                        "insert.fetch.retry",
                        extra={
                            "attempt": attempt + 1,
                            "reason": "http_5xx",
                            "status_code": response.status_code,
                            "sleep_for": sleep_for,
                        },
                    )
                    await asyncio.sleep(sleep_for)
                    continue

                # Terminal: either 2xx, 4xx, or 5xx with retries exhausted.
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise InsertFetchError(
                        RCode.INSERT_FETCH_FAILED,
                        f"GetDrugDoc HTTP {response.status_code} after "
                        f"{attempt + 1} attempts",
                        detail={"params": params, "attempts": attempt + 1},
                    ) from exc
                body = response.content
                break
    finally:
        if rate_limit_interval > 0:
            await asyncio.sleep(rate_limit_interval)

    if body is None:
        # Defensive: loop exited without setting body and without raising.
        # Should be unreachable, but surface clearly if it ever happens.
        raise InsertFetchError(
            RCode.INSERT_FETCH_FAILED,
            "GetDrugDoc retries exhausted without response",
            detail={"params": params, "last_error": str(last_exc)},
        )

    return body


async def fetch_drug_insert(
    *,
    base_url: str,
    license_code: str | None = None,
    s_code: str | None = None,
    startdate: str | None = None,
    enddate: str | None = None,
    rate_limit_interval: float = 0.5,
    timeout: float = 120.0,  # noqa: ASYNC109 — see fetch_drug_insert_bytes
    max_retries: int = 2,
    retry_backoff: float = 0.5,
    throttle: InsertEgressThrottle | None = None,
) -> list[DrugInsert]:
    """Fetch and parse inserts from GetDrugDoc.

    Thin wrapper over `fetch_drug_insert_bytes` for callers that want parsed
    models directly (e.g. `check_insert_updates`). `get_package_insert` does NOT
    use this — it fetches bytes through the ADR-0011 cache and parses on hit.

    Raises:
        InsertFetchError: HTTP failure (after retries) or missing required params.
        InsertParseError: XML parse failure / API <Error> response.
    """
    body = await fetch_drug_insert_bytes(
        base_url=base_url,
        license_code=license_code,
        s_code=s_code,
        startdate=startdate,
        enddate=enddate,
        rate_limit_interval=rate_limit_interval,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        throttle=throttle,
    )
    return parse_get_drug_doc(body)
