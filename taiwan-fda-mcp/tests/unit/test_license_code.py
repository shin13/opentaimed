# path: tests/unit/test_license_code.py
# brief: Verify Chinese license string ↔ 8-digit code transform.

import pytest

from taiwan_fda_mcp.exceptions import (
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
)
from taiwan_fda_mcp.sources.license_code import (
    LICENSE_PREFIX_MAP,
    license_code_to_str,
    license_str_to_code,
)


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


@pytest.mark.parametrize(
    ("license_str", "expected"),
    [
        ("衛署成製字第007884號", "03007884"),  # OTC 安皮露
        ("衛部成製字第016944號", "53016944"),  # OTC 安皮露防蚊液 (live-verified)
        ("內衛成製字第001347號", "14001347"),  # OTC 綠油精 (live-verified)
        ("衛署成輸字第000001號", "19000001"),
        ("衛部成輸字第000001號", "69000001"),
    ],
)
def test_otc_and_extended_prefixes(license_str: str, expected: str) -> None:
    """OTC (成藥) + 衛部/內衛 extended prefixes from ADR-0007 附錄一."""
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


def test_reverse_map_is_unambiguous() -> None:
    """Every 2-digit code maps back to exactly one prefix, so the reverse is well-defined."""
    codes = list(LICENSE_PREFIX_MAP.values())
    assert len(codes) == len(set(codes))


@pytest.mark.parametrize("prefix", sorted(LICENSE_PREFIX_MAP))
def test_round_trip_every_prefix(prefix: str) -> None:
    original = f"{prefix}第048092號"
    assert license_code_to_str(license_str_to_code(original)) == original


def test_known_real_code_from_the_nhi_file() -> None:
    # licId 01049322 appears in tests/fixtures/nhi_drug_items_sample.csv
    assert license_code_to_str("01049322") == "衛署藥製字第049322號"


def test_six_digit_legacy_code_is_rejected() -> None:
    # 328 licIds in the NHI file are six-digit legacy forms with no 許可證 prefix.
    with pytest.raises(InvalidLicenseError):
        license_code_to_str("000238")


def test_unknown_prefix_code_is_rejected() -> None:
    with pytest.raises(LicensePrefixUnsupportedError):
        license_code_to_str("99123456")
