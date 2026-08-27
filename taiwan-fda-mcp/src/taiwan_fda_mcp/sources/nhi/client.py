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
) -> NhiMetadata:
    """Fetch the 2 KB dataset metadata — the cheap freshness signal.

    Measured at 2,020 bytes and 0.51-0.88 s, so this MAY block a query, unlike
    the payload download.

    Args:
        base_url: e.g. 'https://info.nhi.gov.tw'.
        timeout: per-request timeout in seconds.
        rate_limit_interval: seconds to sleep after the request. 0 for tests.

    Returns:
        NhiMetadata with both upstream timestamps and the declared row count.

    Raises:
        DatasetFetchError: transport failure, or a 200 whose body is not the
            expected JSON object (this host returns 200 for several failures).
    """
    url = f"{base_url.rstrip('/')}{NHI_METADATA_PATH}"
    _logger.info("nhi.probe.start", extra={"url": url})
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.text
    except httpx.HTTPError as exc:
        raise DatasetFetchError(
            RCode.DATASET_FETCH_FAILED, f"NHI metadata probe failed: {exc}"
        ) from exc
    finally:
        if rate_limit_interval > 0:
            await asyncio.sleep(rate_limit_interval)

    try:
        payload = json.loads(body)
        distribution = payload["distribution"][0]
        meta = NhiMetadata(
            modified=str(payload["modified"]),
            resource_modified=str(distribution["resourceModified"]),
            number_of_data=int(payload["numberOfData"]),
        )
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            "NHI metadata response is not the expected JSON object",
            detail={"body_prefix": body[:80]},
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
