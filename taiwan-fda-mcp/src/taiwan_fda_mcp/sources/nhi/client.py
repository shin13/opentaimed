# path: src/taiwan_fda_mcp/sources/nhi/client.py
# brief: Async HTTP client for info.nhi.gov.tw — 2 KB metadata probe and 92 MB CSV payload.

import asyncio
import json
import logging

import httpx

from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.models import NhiDrugItem, NhiMetadata
from taiwan_fda_mcp.sources.nhi.dataset import parse_rows, payload_sha256, validate_payload

_logger = logging.getLogger(__name__)

# The dataset identifier and the resource ID are DIFFERENT strings, and the
# metadata route accepts only the former: /rest/dataset/<resource ID> answers
# HTTP 200 with the literal body "Not found".
NHI_DATASET_ID = "A21030000I-E41001"
NHI_RESOURCE_ID = "A21030000I-E41001-001"

NHI_METADATA_PATH = f"/api/iode0010/v1/rest/dataset/{NHI_DATASET_ID}"
# The metadata's own advertised downloadURL (/Dataset?rId=…) is broken — it
# answers 200 with a 172-byte JS redirect. This path is the working one.
NHI_DOWNLOAD_PATH = f"/api/iode0000s01/Dataset?rId={NHI_RESOURCE_ID}"

_DOWNLOAD_TIMEOUT_SECONDS = 600.0


async def probe_metadata(
    base_url: str,
    timeout: float = 5.0,  # noqa: ASYNC109
    rate_limit_interval: float = 0.0,
    max_retries: int = 1,
    retry_backoff: float = 0.5,
) -> NhiMetadata:
    """Fetch the 2 KB dataset metadata — the cheap freshness signal.

    Measured at 2,020 bytes and 0.51-0.88 s, so this MAY block a query, unlike
    the payload download.

    Failures are split four ways because each implies a different fix, and the
    RCode is what tells a human at 02:00 which one to reach for:

    | RCode                  | Meaning                          | Fix |
    |------------------------|----------------------------------|-----|
    | `DATASET_FETCH_FAILED` | no response at all, retries gone | upstream availability — confirm by hand |
    | `DATASET_HTTP_STATUS`  | a response, non-2xx (e.g. 404)   | the endpoint moved — re-verify URL / dataset ID |
    | `DATASET_EMPTY`        | 2xx, but carrying no dataset     | investigate: the ID may be retired |
    | `DATASET_PARSE_FAILED` | a dataset, but a field changed   | upstream schema drift — fix the parsing below |

    Only the first is retried: `httpx.RequestError` means no valid HTTP response
    was received, which is the transient class. Note that it is deliberately
    broader than `httpx.TimeoutException` — the two live failures (2026-09-02,
    2026-09-06) were `httpx.ReadError`, a connection dropped mid-read, which is
    NOT a timeout; retrying timeouts alone would have missed both.

    **Keep `max_retries` low here.** `NhiItemStore._probe_and_maybe_schedule`
    awaits this while holding the store lock, and a failed probe leaves the memo
    stale, so every subsequent query re-probes. Worst-case blocking per query
    while the host is unreachable is `timeout * (max_retries + 1)` plus backoff
    — 10.5 s at the 5 s default and one retry, and it queues. The live smoke
    test overrides this (3 attempts, wider backoff): CI can afford the wait and
    no query is behind it.

    Args:
        base_url: e.g. 'https://info.nhi.gov.tw'.
        timeout: per-request timeout in seconds.
        rate_limit_interval: seconds to sleep after the (final) request. 0 for tests.
        max_retries: retry attempts on transport failure (default 1, i.e. up to
            2 HTTP calls). See the blocking note above before raising it.
        retry_backoff: base sleep seconds between retries; doubles each attempt.

    Returns:
        NhiMetadata with both upstream timestamps and the declared row count.

    Raises:
        DatasetFetchError: always, carrying one of the four RCodes above.
    """
    url = f"{base_url.rstrip('/')}{NHI_METADATA_PATH}"
    _logger.info("nhi.probe.start", extra={"url": url})
    attempt = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            while True:
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                except httpx.RequestError as exc:
                    if attempt >= max_retries:
                        raise DatasetFetchError(
                            RCode.DATASET_FETCH_FAILED,
                            f"NHI metadata probe unreachable after {attempt + 1} attempts "
                            f"({type(exc).__name__}: {exc}) — upstream availability, "
                            "not schema drift; confirm the host by hand",
                            detail={"url": url, "attempts": attempt + 1},
                        ) from exc
                    sleep_for = retry_backoff * (2**attempt)
                    _logger.warning(
                        "nhi.probe.retry",
                        extra={
                            "attempt": attempt + 1,
                            "error": str(exc),
                            "sleep_for": sleep_for,
                        },
                    )
                    await asyncio.sleep(sleep_for)
                    attempt += 1
                except httpx.HTTPStatusError as exc:
                    raise DatasetFetchError(
                        RCode.DATASET_HTTP_STATUS,
                        f"NHI metadata probe answered HTTP {exc.response.status_code} — "
                        "the endpoint moved or the dataset ID is wrong; re-verify "
                        f"NHI_DATASET_ID ({NHI_DATASET_ID}) against info.nhi.gov.tw",
                        detail={"url": url, "status_code": exc.response.status_code},
                    ) from exc
                else:
                    body = response.text
                    break
    finally:
        if rate_limit_interval > 0:
            await asyncio.sleep(rate_limit_interval)

    # Reached the host and got a 2xx, so the remaining two failure modes are
    # about CONTENT, and they are split: "no dataset came back" is something to
    # investigate upstream, whereas "a dataset came back but a field moved" is
    # a change this parser has to follow.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise DatasetFetchError(
            RCode.DATASET_EMPTY,
            "NHI metadata answered 2xx with a non-JSON body — this host serves "
            "HTTP 200 + 'Not found' for a retired or mistyped dataset ID; "
            f"investigate whether {NHI_DATASET_ID} still exists",
            detail={"url": url, "body_prefix": body[:80]},
        ) from exc

    if not isinstance(payload, dict):
        raise DatasetFetchError(
            RCode.DATASET_EMPTY,
            "NHI metadata answered 2xx with JSON that is not an object — no "
            f"dataset was returned; investigate whether {NHI_DATASET_ID} still exists",
            detail={"url": url, "body_prefix": body[:80]},
        )

    try:
        distribution = payload["distribution"][0]
        meta = NhiMetadata(
            modified=str(payload["modified"]),
            resource_modified=str(distribution["resourceModified"]),
            number_of_data=int(payload["numberOfData"]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            "NHI metadata is missing an expected field — upstream changed its "
            "schema; the parsing in sources/nhi/client.py needs to follow it "
            f"({type(exc).__name__}: {exc})",
            detail={"url": url, "body_prefix": body[:80]},
        ) from exc

    _logger.info(
        "nhi.probe.done",
        extra={
            "modified": meta.modified,
            "resource_modified": meta.resource_modified,
            "upstream_rows": meta.number_of_data,
        },
    )
    return meta


async def fetch_drug_items(
    base_url: str,
    timeout: float = _DOWNLOAD_TIMEOUT_SECONDS,  # noqa: ASYNC109
    rate_limit_interval: float = 0.0,
) -> tuple[list[NhiDrugItem], str, int]:
    """Download and parse the full drug-item CSV (92 MB, >120 s measured).

    Never call this on a query path — only from a background refresh or a cold
    start. All three integrity gates run before the rows are returned.

    Returns:
        (rows, payload_sha256, byte_count).

    Raises:
        DatasetFetchError: transport failure or any integrity gate failing.
    """
    url = f"{base_url.rstrip('/')}{NHI_DOWNLOAD_PATH}"
    _logger.info("nhi.download.start", extra={"url": url})
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw = response.content
            declared = response.headers.get("Content-Length")
    except httpx.HTTPError as exc:
        raise DatasetFetchError(
            RCode.DATASET_FETCH_FAILED, f"NHI payload download failed: {exc}"
        ) from exc
    finally:
        if rate_limit_interval > 0:
            await asyncio.sleep(rate_limit_interval)

    text = validate_payload(raw, int(declared) if declared is not None else None)
    rows = parse_rows(text)
    digest = payload_sha256(raw)
    _logger.info(
        "nhi.download.done",
        extra={"bytes": len(raw), "current_rows": len(rows), "sha256": digest},
    )
    return rows, digest, len(raw)
