# path: src/taiwan_fda_mcp/sources/license_code.py
# brief: Map Chinese drug license strings to 8-digit GetDrugDoc API codes.

import re

from taiwan_fda_mcp.exceptions import (
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)

LICENSE_PREFIX_MAP: dict[str, str] = {
    "衛署藥製字": "01",
    "衛署藥輸字": "02",
    "內衛藥製字": "12",
    "衛部藥製字": "51",
    "衛部藥輸字": "52",
    "衛部菌疫輸字": "60",
    "衛部罕藥製字": "71",
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
