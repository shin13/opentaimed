# path: tests/unit/test_nhi_dataset.py
# brief: Parsing, ROC dates, and status derivation for the NHI drug-item file.

import hashlib
from pathlib import Path

import pytest

from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.models import NhiCacheMeta
from taiwan_fda_mcp.sources.nhi.dataset import (
    EXPECTED_COLUMNS,
    cache_mtime,
    load_from_cache,
    parse_rows,
    payload_sha256,
    read_meta,
    roc_to_iso,
    validate_payload,
    write_meta,
    write_to_cache,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "nhi_drug_items_sample.csv"


@pytest.fixture
def rows():
    return parse_rows(_FIXTURE.read_text(encoding="utf-8"))


def test_only_current_rows_survive(rows):
    """The 1120401→1130331 row for AC49322100 is historical and must be dropped."""
    assert len(rows) == 7  # noqa: PLR2004
    assert all(r.effective_end is None for r in rows)
    prices = [r.price for r in rows if r.nhi_code == "AC49322100"]
    assert prices == [8.6]  # the 9.90 historical row is gone


def test_reimbursed_row(rows):
    row = next(r for r in rows if r.nhi_code == "AC49322100")
    assert row.reimbursement_status == "reimbursed"
    assert row.price == 8.6  # noqa: PLR2004
    assert row.price_raw == "8.60"
    assert row.effective_start == "2024-04-01"
    assert row.license_no == "衛署藥製字第049322號"
    assert row.payment_rule_sections == ["1.2.1.1."]  # trailing dot preserved
    assert row.atc_code == "N06AX12"


def test_delisted_row_has_no_price(rows):
    row = next(r for r in rows if r.nhi_code == "AC51728100")
    assert row.reimbursement_status == "delisted"
    assert row.price is None
    assert row.price_raw == "0.00"


def test_not_priced_row_preserves_the_raw_dash(rows):
    row = next(r for r in rows if r.nhi_code == "BC24368100")
    assert row.reimbursement_status == "not_priced"
    assert row.price is None
    assert row.price_raw == "-"


def test_empty_chapter_is_an_empty_list(rows):
    row = next(r for r in rows if r.nhi_code == "AC44577100")
    assert row.payment_rule_sections == []
    assert row.payment_rule_urls == []


def test_multi_valued_chapters_split_on_comma(rows):
    row = next(r for r in rows if r.nhi_code == "AC48058100")
    assert row.payment_rule_sections == ["1.2.2.1.", "1.2.2.2."]
    assert len(row.payment_rule_urls) == 2  # noqa: PLR2004
    assert all(u.startswith("https://info.nhi.gov.tw/") for u in row.payment_rule_urls)


def test_six_digit_legacy_licid_yields_no_license_no(rows):
    row = next(r for r in rows if r.nhi_code == "X000238100")
    assert row.license_no is None


def test_fan_out_two_codes_share_one_licence(rows):
    codes = sorted(r.nhi_code for r in rows if r.license_no == "衛署藥製字第049322號")
    assert codes == ["A049322100", "AC49322100"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1130401", "2024-04-01"),
        ("840301", "1995-03-01"),  # six-digit early form
        ("9991231", None),  # the "no end date" sentinel
        ("", None),
        ("   ", None),
    ],
)
def test_roc_to_iso(raw: str, expected: str | None):
    assert roc_to_iso(raw) == expected


def test_bom_is_stripped():
    """The live download is UTF-8 with BOM; the first column name must still match."""
    rows_with_bom = parse_rows("﻿" + _FIXTURE.read_text(encoding="utf-8"))
    assert len(rows_with_bom) == 7  # noqa: PLR2004


def _good_bytes() -> bytes:
    return _FIXTURE.read_text(encoding="utf-8").encode("utf-8")


def test_validate_payload_accepts_a_good_body():
    raw = _good_bytes()
    assert validate_payload(raw, len(raw)) == raw.decode("utf-8")


def test_validate_payload_rejects_a_truncated_transfer():
    raw = _good_bytes()
    with pytest.raises(DatasetFetchError, match="truncated"):
        validate_payload(raw, len(raw) + 1)


def test_validate_payload_skips_the_length_gate_when_the_header_is_absent():
    raw = _good_bytes()
    assert validate_payload(raw, None) == raw.decode("utf-8")


def test_validate_payload_rejects_http_200_with_a_wrong_body():
    """Observed three times on this API — a 200 is never success on its own."""
    body = b"Not found"
    with pytest.raises(DatasetFetchError, match="column"):
        validate_payload(body, len(body))


def test_validate_payload_rejects_a_header_only_file():
    body = (",".join(EXPECTED_COLUMNS) + "\n").encode("utf-8")
    with pytest.raises(DatasetFetchError, match="no current rows"):
        validate_payload(body, len(body))


def test_payload_sha256_matches_hashlib():
    raw = _good_bytes()
    assert payload_sha256(raw) == hashlib.sha256(raw).hexdigest()


def test_cache_round_trip(tmp_path):
    rows = parse_rows(_FIXTURE.read_text(encoding="utf-8"))
    write_to_cache(rows, tmp_path)
    assert load_from_cache(tmp_path) == rows
    assert cache_mtime(tmp_path) is not None


def test_load_from_cache_returns_none_when_absent(tmp_path):
    assert load_from_cache(tmp_path) is None
    assert read_meta(tmp_path) is None
    assert cache_mtime(tmp_path) is None


def test_meta_round_trip(tmp_path):
    meta = NhiCacheMeta(
        payload_sha256="a" * 64,
        content_length=96843587,
        row_count=224553,
        modified="2026-08-10T15:25:14",
        resource_modified="2026-07-28 07:01:52",
        downloaded_at="2026-08-27T03:00:00+00:00",
    )
    write_meta(meta, tmp_path)
    assert read_meta(tmp_path) == meta
