# path: src/taiwan_fda_mcp/sources/opendata/client.py
# brief: Async HTTP client for data.fda.gov.tw open-data exports.

import io
import json
import logging
import zipfile

import httpx

from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.models import DrugLicense
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows

_logger = logging.getLogger(__name__)

_DATASET37_PATH = "/data/opendata/export/37/json"


async def fetch_dataset37(base_url: str, timeout: float = 60.0) -> list[DrugLicense]:  # noqa: ASYNC109
    """Download, unzip, and parse the Dataset 37 export.

    Args:
        base_url: e.g. 'https://data.fda.gov.tw'
        timeout: per-request timeout in seconds.

    Returns:
        List of DrugLicense.

    Raises:
        DatasetFetchError: HTTP failure, malformed zip, or parse failure.
    """
    url = f"{base_url.rstrip('/')}{_DATASET37_PATH}"
    _logger.info("dataset37.fetch.start", extra={"url": url})

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            zip_bytes = response.content
    except httpx.HTTPError as exc:
        raise DatasetFetchError(
            RCode.DATASET_FETCH_FAILED,
            f"Failed to download Dataset 37: {exc}",
        ) from exc

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            json_member = next(
                (n for n in zf.namelist() if n.endswith(".json")),
                None,
            )
            if json_member is None:
                raise DatasetFetchError(
                    RCode.DATASET_PARSE_FAILED,
                    "Dataset 37 ZIP contains no .json member",
                )
            raw_json = zf.read(json_member)
    except zipfile.BadZipFile as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            "Dataset 37 download is not a valid ZIP",
        ) from exc

    try:
        raw_rows = json.loads(raw_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            "Dataset 37 JSON could not be decoded",
        ) from exc

    rows = parse_rows(raw_rows)
    _logger.info("dataset37.fetch.done", extra={"count": len(rows)})
    return rows
