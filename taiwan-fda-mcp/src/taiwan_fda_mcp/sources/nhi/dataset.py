# path: src/taiwan_fda_mcp/sources/nhi/dataset.py
# brief: Parse the NHI drug-item CSV, manage its on-disk cache, and gate payload integrity.

import csv
import io
import re
from typing import Literal

from taiwan_fda_mcp.exceptions import (
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
)
from taiwan_fda_mcp.models import NhiDrugItem
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
