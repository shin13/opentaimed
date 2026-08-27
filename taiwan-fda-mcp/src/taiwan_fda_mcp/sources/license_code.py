# path: src/taiwan_fda_mcp/sources/license_code.py
# brief: Map Chinese drug license strings to 8-digit GetDrugDoc API codes.

import re

from taiwan_fda_mcp.exceptions import (
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)

# Full prefix → 2-digit code table, per ADR-0007 附錄一 (我國藥品仿單電子格式規範).
# Covers Rx, OTC (成藥), biologics (菌疫), orphan (罕藥), and legacy 內衛 series.
LICENSE_PREFIX_MAP: dict[str, str] = {
    # 衛署 series
    "衛署藥製字": "01",
    "衛署藥輸字": "02",
    "衛署成製字": "03",
    "衛署菌疫製字": "09",
    "衛署菌疫輸字": "10",
    "衛署成輸字": "19",
    "衛署罕藥輸字": "20",
    "衛署罕藥製字": "21",
    "衛署罕菌疫輸字": "22",
    "衛署罕菌疫製字": "23",
    "衛署藥陸輸字": "41",
    # 衛部 series
    "衛部藥製字": "51",
    "衛部藥輸字": "52",
    "衛部成製字": "53",
    "衛部菌疫製字": "59",
    "衛部菌疫輸字": "60",
    "衛部成輸字": "69",
    "衛部罕藥輸字": "70",
    "衛部罕藥製字": "71",
    "衛部罕菌疫輸字": "72",
    "衛部罕菌疫製字": "73",
    "衛部藥陸輸字": "91",
    # 內衛 series (legacy)
    "內衛藥製字": "12",
    "內衛藥輸字": "13",
    "內衛成製字": "14",
    "內衛菌疫製字": "15",
    "內衛菌疫輸字": "16",
}

_LICENSE_RE = re.compile(r"^(\D+字)第(\d+)號$")


def license_str_to_code(license_str: str) -> str:
    """Convert '衛署藥輸字第021571號' to '02021571'.

    Args:
        license_str: Full Chinese license string.

    Returns:
        8-digit license code accepted by mcp.fda.gov.tw GetDrugDoc.

    Raises:
        InvalidLicenseError: input does not match the expected shape.
        LicensePrefixUnsupportedError: prefix not in the verified mapping table.
    """
    if not license_str:
        raise InvalidLicenseError(
            RCode.INVALID_LICENSE,
            "Empty license string",
        )

    match = _LICENSE_RE.match(license_str)
    if not match:
        raise InvalidLicenseError(
            RCode.INVALID_LICENSE,
            f"License string does not match expected pattern: {license_str!r}",
        )

    prefix_str, number = match.groups()
    prefix_code = LICENSE_PREFIX_MAP.get(prefix_str)
    if not prefix_code:
        raise LicensePrefixUnsupportedError(
            RCode.LICENSE_PREFIX_UNSUPPORTED,
            f"License prefix not in verified mapping table: {prefix_str!r}",
            detail={"supported_prefixes": list(LICENSE_PREFIX_MAP)},
        )

    return f"{prefix_code}{number.zfill(6)}"


# Reverse of LICENSE_PREFIX_MAP. Safe to build mechanically: all 27 codes are
# distinct (asserted by test_reverse_map_is_unambiguous), so the inverse is a
# function, not a relation.
LICENSE_CODE_TO_PREFIX: dict[str, str] = {v: k for k, v in LICENSE_PREFIX_MAP.items()}

_LICENSE_CODE_RE = re.compile(r"^\d{8}$")


def license_code_to_str(code: str) -> str:
    """Convert '02021571' back to '衛署藥輸字第021571號'.

    The inverse of `license_str_to_code`. Needed because the NHI drug file
    identifies a licence only by the 8-digit code embedded in its
    藥品代碼超連結, while every other tool in this server speaks the full
    Chinese licence string.

    Args:
        code: 8-digit licence code (2-digit prefix + 6-digit serial).

    Returns:
        Full Chinese licence string.

    Raises:
        InvalidLicenseError: not exactly 8 digits — including the six-digit
            legacy codes carried by 328 rows of the NHI file, which belong to a
            different numbering space and cannot be mapped.
        LicensePrefixUnsupportedError: 2-digit prefix not in the verified table.
    """
    value = (code or "").strip()
    if not _LICENSE_CODE_RE.match(value):
        raise InvalidLicenseError(
            RCode.INVALID_LICENSE,
            f"License code must be exactly 8 digits: {code!r}",
        )

    prefix = LICENSE_CODE_TO_PREFIX.get(value[:2])
    if prefix is None:
        raise LicensePrefixUnsupportedError(
            RCode.LICENSE_PREFIX_UNSUPPORTED,
            f"License code prefix not in verified mapping table: {value[:2]!r}",
            detail={"supported_codes": sorted(LICENSE_CODE_TO_PREFIX)},
        )

    return f"{prefix}第{value[2:]}號"
