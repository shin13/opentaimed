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
