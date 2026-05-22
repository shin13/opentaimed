# path: tests/unit/test_license_code.py
# brief: Verify Chinese license string ↔ 8-digit code transform.

import pytest

from taiwan_fda_mcp.exceptions import (
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
)
from taiwan_fda_mcp.sources.license_code import license_str_to_code


@pytest.mark.parametrize(
    ("license_str", "expected"),
    [
        ("衛署藥製字第020058號", "01020058"),
        ("衛署藥輸字第021571號", "02021571"),
        ("內衛藥製字第000847號", "12000847"),
        ("衛部藥製字第059832號", "51059832"),
        ("衛部藥輸字第026701號", "52026701"),
        ("衛部菌疫輸字第001195號", "60001195"),
        ("衛部罕藥製字第000020號", "71000020"),
    ],
)
def test_known_prefixes(license_str: str, expected: str) -> None:
    assert license_str_to_code(license_str) == expected


def test_zero_pads_short_numbers() -> None:
    """6-digit number is zero-padded to width 6."""
    assert license_str_to_code("衛署藥製字第0058號") == "01000058"


def test_unsupported_prefix_raises() -> None:
    with pytest.raises(LicensePrefixUnsupportedError):
        license_str_to_code("衛部中藥製字第000001號")


def test_malformed_input_raises() -> None:
    with pytest.raises(InvalidLicenseError):
        license_str_to_code("not a license")
    with pytest.raises(InvalidLicenseError):
        license_str_to_code("")
