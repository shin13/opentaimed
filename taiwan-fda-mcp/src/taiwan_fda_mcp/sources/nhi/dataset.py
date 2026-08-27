# path: src/taiwan_fda_mcp/sources/nhi/dataset.py
# brief: Parse the NHI drug-item CSV, manage its on-disk cache, and gate payload integrity.

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Literal

from taiwan_fda_mcp.exceptions import (
    DatasetFetchError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)
from taiwan_fda_mcp.models import NhiCacheMeta, NhiDrugItem
from taiwan_fda_mcp.sources.license_code import license_code_to_str

# The 20 columns the upstream CSV must carry, in order. Used as a content
# contract: this API has been observed returning HTTP 200 with a wrong body
# three separate ways, so a 200 is never treated as success on its own.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "異動",
    "藥品代號",
    "藥品英文名稱",
    "藥品中文名稱",
    "成分",
    "規格量",
    "規格單位",
    "單複方",
    "支付價",
    "有效起日",
    "有效迄日",
    "藥商",
    "製造廠名稱",
    "劑型",
    "藥品分類",
    "分類分組名稱",
    "ATC代碼",
    "給付規定章節",
    "藥品代碼超連結",
    "給付規定章節連結",
)

# 有效迄日 value marking a row as currently in force (no end date).
_CURRENT_SENTINEL = "9991231"

_LICID_RE = re.compile(r"licId=(\w+)")


def roc_to_iso(value: str) -> str | None:
    """Convert an ROC-era date such as '1130401' or '840301' to an ISO date.

    Args:
        value: ROC date, six or seven digits (YYYMMDD, year zero-padded).

    Returns:
        ISO 'YYYY-MM-DD', or None for an empty value or the 9991231 sentinel
        (which means "no end date", not a date in the year 999).
    """
    raw = (value or "").strip()
    if not raw or raw == _CURRENT_SENTINEL:
        return None
    padded = raw.zfill(7)
    year = int(padded[:3]) + 1911
    return f"{year:04d}-{padded[3:5]}-{padded[5:7]}"


def _status_and_price(
    raw: str,
) -> tuple[Literal["reimbursed", "delisted", "not_priced"], float | None]:
    """Derive (reimbursement_status, price) from the 支付價 cell.

    Three states, measured over the 45,175 current rows: 13,916 carry a positive
    price, 31,212 carry '0.00' (停止給付 — NOT a free drug), and 47 carry the
    literal '-'. NHI does not document what '-' means, so it maps to
    'not_priced' with no reason attached.
    """
    value = (raw or "").strip()
    try:
        price = float(value)
    except ValueError:
        return "not_priced", None
    return ("reimbursed", price) if price > 0 else ("delisted", None)


def _license_from_url(url: str) -> str | None:
    """Extract the 8-digit licId from 藥品代碼超連結 and expand it to a 許可證字號.

    The URL is parsed as a string only — lmspiq is never contacted, so ADR-0003's
    rejection of lmspiq scraping is untouched. Returns None for the 328 six-digit
    legacy codes and for any prefix outside the verified table.
    """
    match = _LICID_RE.search(url or "")
    if match is None:
        return None
    try:
        return license_code_to_str(match.group(1))
    except (InvalidLicenseError, LicensePrefixUnsupportedError):
        return None


def _split_list(cell: str) -> list[str]:
    """Split a comma-separated cell, preserving each value verbatim.

    Chapter codes keep their trailing dot ('1.2.1.'), matching how NHI writes
    them — the same faithfulness rule search_by_ingredient applies to 主成分略述.
    """
    return [part.strip() for part in (cell or "").split(",") if part.strip()]


def parse_rows(text: str) -> list[NhiDrugItem]:
    """Parse the NHI drug-item CSV, keeping only currently-effective rows.

    Args:
        text: full CSV text; a leading UTF-8 BOM is tolerated (the live
            download carries one).

    Returns:
        One NhiDrugItem per 藥品代號 whose 有效迄日 is the 9991231 sentinel.
        Historical price rows are dropped.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    result: list[NhiDrugItem] = []
    for row in reader:
        if (row.get("有效迄日") or "").strip() != _CURRENT_SENTINEL:
            continue
        status, price = _status_and_price(row.get("支付價", ""))
        result.append(
            NhiDrugItem(
                nhi_code=(row.get("藥品代號") or "").strip(),
                name_zh=(row.get("藥品中文名稱") or "").strip(),
                name_en=(row.get("藥品英文名稱") or "").strip(),
                ingredient=(row.get("分類分組名稱") or "").strip(),
                form=(row.get("劑型") or "").strip(),
                spec_amount=(row.get("規格量") or "").strip(),
                spec_unit=(row.get("規格單位") or "").strip(),
                single_or_compound=(row.get("單複方") or "").strip(),
                drug_classify=(row.get("藥品分類") or "").strip(),
                atc_code=(row.get("ATC代碼") or "").strip(),
                reimbursement_status=status,
                price=price,
                price_raw=(row.get("支付價") or "").strip(),
                effective_start=roc_to_iso(row.get("有效起日", "")),
                effective_end=roc_to_iso(row.get("有效迄日", "")),
                vendor=(row.get("藥商") or "").strip(),
                manufacturer=(row.get("製造廠名稱") or "").strip(),
                payment_rule_sections=_split_list(row.get("給付規定章節", "")),
                payment_rule_urls=_split_list(row.get("給付規定章節連結", "")),
                license_no=_license_from_url(row.get("藥品代碼超連結", "")),
            )
        )
    return result


_CACHE_FILE = "nhi_items.json"
_META_FILE = "nhi_meta.json"


def payload_sha256(raw: bytes) -> str:
    """Hex sha256 of the raw payload — the cache's version identity."""
    return hashlib.sha256(raw).hexdigest()


def validate_payload(raw: bytes, content_length: int | None) -> str:
    """Run all three integrity gates and return the decoded CSV text.

    Gate 1 — byte count against Content-Length, catching a truncated transfer.
    Skipped when the header is absent rather than assumed.
    Gate 2 — the header row against EXPECTED_COLUMNS, catching HTTP 200 with a
    wrong body.
    Gate 3 — at least one currently-effective row, catching a well-formed but
    empty file.

    `numberOfData` is deliberately NOT a gate: it is a semantic count that grows
    when NHI adds rows, and the probe-to-download window is over two minutes
    wide, so equality would fail on exactly the good case.

    Raises:
        DatasetFetchError: any gate fails.
    """
    if content_length is not None and len(raw) != content_length:
        raise DatasetFetchError(
            RCode.DATASET_FETCH_FAILED,
            "NHI payload truncated",
            detail={"expected_bytes": content_length, "received_bytes": len(raw)},
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DatasetFetchError(RCode.DATASET_PARSE_FAILED, "NHI payload is not UTF-8") from exc

    header = next(csv.reader(io.StringIO(text)), [])
    if tuple(h.strip() for h in header) != EXPECTED_COLUMNS:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            "NHI payload column header does not match the expected 20 columns",
            detail={"received_header": header[:5]},
        )

    if not parse_rows(text):
        raise DatasetFetchError(RCode.DATASET_PARSE_FAILED, "NHI payload contains no current rows")

    return text


def write_to_cache(rows: list[NhiDrugItem], cache_dir: Path) -> None:
    """Persist parsed rows to the JSON cache file."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / _CACHE_FILE).write_text(
        json.dumps([r.model_dump() for r in rows], ensure_ascii=False),
        encoding="utf-8",
    )


def load_from_cache(cache_dir: Path) -> list[NhiDrugItem] | None:
    """Load cached rows, or None if the cache file is missing."""
    path = cache_dir / _CACHE_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DatasetFetchError(
            RCode.DATASET_PARSE_FAILED,
            f"Corrupt NHI item cache at {path}",
            detail={"error": str(exc)},
        ) from exc
    return [NhiDrugItem(**row) for row in payload]


def write_meta(meta: NhiCacheMeta, cache_dir: Path) -> None:
    """Persist the version sidecar next to the item cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / _META_FILE).write_text(meta.model_dump_json(), encoding="utf-8")


def read_meta(cache_dir: Path) -> NhiCacheMeta | None:
    """Read the version sidecar, or None if it is missing or unreadable.

    A missing sidecar is not an error: it simply forces the next refresh to
    download rather than trust a timestamp comparison.
    """
    path = cache_dir / _META_FILE
    if not path.exists():
        return None
    try:
        return NhiCacheMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def cache_mtime(cache_dir: Path) -> float | None:
    """Epoch mtime of the item cache file, or None if absent."""
    path = cache_dir / _CACHE_FILE
    return path.stat().st_mtime if path.exists() else None
