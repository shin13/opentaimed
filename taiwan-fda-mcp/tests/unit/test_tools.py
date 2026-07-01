# path: tests/unit/test_tools.py
# brief: Verify tools.py entry-point behaviour.

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

import taiwan_fda_mcp.tools as _tools_mod
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError, RCode
from taiwan_fda_mcp.models import DrugInsert
from taiwan_fda_mcp.sources.insert.cache import get_insert_cache
from taiwan_fda_mcp.sources.insert.throttle import get_insert_throttle
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows, write_to_cache
from taiwan_fda_mcp.tool_responses import GetPackageInsertResponse
from taiwan_fda_mcp.tools import (
    check_insert_updates,
    get_package_insert,
    search_drugs,
)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Clear tools.py module-level Dataset 37 SWR state between tests."""
    _tools_mod._LICENSES_CACHE = None
    _tools_mod._LICENSES_LOADED_AT = None
    _tools_mod._REFRESH_TASK = None


def make_settings(*, cache_dir: Path, ttl_hours: int = 24) -> Settings:
    """Settings pointed at a temp cache dir, with no real rate-limit delay."""
    return Settings(  # type: ignore[call-arg]
        DATASET37_CACHE_DIR=cache_dir,
        DATASET37_TTL_HOURS=ttl_hours,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
    )


@pytest.fixture
def seeded_settings(tmp_path: Path, fixtures_dir: Path) -> Settings:
    """Settings with a pre-seeded Dataset 37 cache so tools never hit the network for it."""
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    rows = parse_rows(raw)
    cache_dir = tmp_path / "ds37"
    write_to_cache(rows, cache_dir)
    return Settings(  # type: ignore[call-arg]
        DATASET37_CACHE_DIR=cache_dir,
        DATASET37_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
        INSERT_THROTTLE_MIN_INTERVAL_SECONDS=0.0,
    )


def test_get_package_insert_response_has_cache_fields():
    r = GetPackageInsertResponse(license_no="X")
    assert r.from_cache is False  # safe default
    assert r.cache_age_hours is None
    r2 = GetPackageInsertResponse(license_no="X", from_cache=True, cache_age_hours=1.5)
    assert r2.from_cache is True
    assert r2.cache_age_hours == 1.5  # noqa: PLR2004


@pytest.mark.asyncio
async def test_search_drugs_returns_envelope_with_total(seeded_settings):
    response = (await search_drugs(query="脈優", settings=seeded_settings)).model_dump()
    assert response["query"] == "脈優"
    assert response["error"] is None
    assert response["total_matched"] == 1
    assert response["returned"] == 1
    assert response["truncated"] is False
    row = response["results"][0]
    assert row["license_no"] == "衛署藥輸字第021571號"
    assert row["name_zh"] == "脈優錠５毫克"
    assert row["manufacturers"] == ["久裕企業股份有限公司"]
    assert "manufacturer" not in row
    assert row["country"] == "IT"


@pytest.mark.asyncio
async def test_search_drugs_signals_truncation(seeded_settings):
    """When limit < total, response must signal truncation so caller can paginate."""
    response = (await search_drugs(query="錠", limit=1, settings=seeded_settings)).model_dump()
    assert response["total_matched"] >= 1
    if response["total_matched"] > 1:
        assert response["truncated"] is True
        assert response["returned"] == 1


@pytest.mark.asyncio
async def test_get_package_insert_key_fields(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()

    assert result["license_no"] == "衛署藥輸字第021571號"
    fields = result["fields"]
    assert "indication" in fields
    assert "高血壓" in fields["indication"]
    assert "dosage" in fields
    assert "5 mg" in fields["dosage"]
    # warnings now maps purely to §5 (no longer merges the top-level <WARNING>).
    assert "warnings" in fields
    assert "葡萄柚汁" in fields["warnings"]
    assert "心衰竭" not in fields["warnings"]
    # The BBW (top-level <WARNING>) is its own field and is in the default set.
    assert "special_warning" in fields
    assert "心衰竭" in fields["special_warning"]
    assert "side_effects" in fields
    assert "頭痛" in fields["side_effects"]

    assert result["source_url"].endswith("license=02021571&s_code=&startdate=&enddate=")
    assert result["human_url"] == (
        "https://mcp.fda.gov.tw/im_detail_1/"
        "%E8%A1%9B%E7%BD%B2%E8%97%A5%E8%BC%B8%E5%AD%97%E7%AC%AC021571%E8%99%9F"
    )
    assert result["last_update_date"] == "2025-10-29"
    datetime.fromisoformat(result["retrieved_at"])

    # Section paths satisfy spec §14 citation requirement.
    assert result["field_sections"]["indication"] == "2"
    assert result["field_sections"]["dosage"] == "3"
    assert result["field_sections"]["warnings"] == "5"
    assert result["field_sections"]["side_effects"] == "8"

    # Attribution: data is official, wrapper is not.
    assert result["attribution"]["data_official"] is True
    assert "NOT a TFDA product" in result["attribution"]["wrapper"]


@pytest.mark.asyncio
async def test_get_package_insert_explicit_fields(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["indication", "name_zh", "manufacturer"],
                settings=seeded_settings,
            )
        ).model_dump(exclude_none=True)
    assert set(result["fields"]) == {"indication", "name_zh", "manufacturer"}
    assert result["fields"]["name_zh"] == "脈優錠５毫克"
    assert result["fields"]["manufacturer"] == "久裕企業股份有限公司"
    assert "unknown_fields" not in result


@pytest.mark.asyncio
async def test_get_package_insert_unknown_field_surfaces_error(seeded_settings, fixtures_dir):
    """Unknown field names must be surfaced in the response, not silently dropped.

    Otherwise the caller (an LLM) has no signal to correct itself and ends up
    re-fetching with fields="all", wasting tokens.
    """
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["indication", "contraindication", "bogus"],  # singular typo + nonsense
                settings=seeded_settings,
            )
        ).model_dump()
    assert "indication" in result["fields"]
    inputs = [u["input"] for u in result["unknown_fields"]]
    assert inputs == ["contraindication", "bogus"]
    # `contraindication` (singular) is one letter off → did_you_mean should catch it.
    by_input = {u["input"]: u["did_you_mean"] for u in result["unknown_fields"]}
    assert by_input["contraindication"] == "contraindications"
    # `bogus` has no close match → did_you_mean is None.
    assert by_input["bogus"] is None


@pytest.mark.asyncio
async def test_get_package_insert_new_section_fields(seeded_settings, fixtures_dir):
    """All 8 newly-mapped sections (1.2, 6, 9, 12, 13.2, 13.4, 14, 15) extract correctly."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=[
                    "excipients",
                    "special_populations",
                    "overdose",
                    "clinical_trials",
                    "shelf_life",
                    "storage_cautions",
                    "patient_instructions",
                    "other_info",
                ],
                settings=seeded_settings,
            )
        ).model_dump()

    fields = result["fields"]
    sections = result["field_sections"]

    assert "微晶纖維素" in fields["excipients"]
    assert sections["excipients"] == "1.2"
    # section 6 parent now folds 6.1 through 6.8; assert a fragment from the sub-sections.
    assert "避免使用" in fields["special_populations"]
    assert sections["special_populations"] == "6"
    assert "支持性治療" in fields["overdose"]
    assert sections["overdose"] == "9"
    assert "ALLHAT" in fields["clinical_trials"]
    assert sections["clinical_trials"] == "12"
    assert "36 個月" in fields["shelf_life"]
    assert sections["shelf_life"] == "13.2"
    assert "避光" in fields["storage_cautions"]
    assert sections["storage_cautions"] == "13.4"
    assert "葡萄柚汁" in fields["patient_instructions"]
    assert sections["patient_instructions"] == "14"
    assert "健保用藥" in fields["other_info"]
    assert sections["other_info"] == "15"


@pytest.mark.asyncio
async def test_get_package_insert_key_fields_includes_excipients(seeded_settings, fixtures_dir):
    """excipients is in KEY_FIELDS so default calls surface allergens."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()
    assert "excipients" in result["fields"]
    assert "微晶纖維素" in result["fields"]["excipients"]


@pytest.mark.asyncio
async def test_get_package_insert_unsupported_prefix(seeded_settings):
    result = (
        await get_package_insert(
            license_no="衛部中藥製字第000001號",
            settings=seeded_settings,
        )
    ).model_dump(exclude_none=True, exclude_defaults=True)
    # Unified error contract: error dict populated, payload defaults excluded so
    # they don't leak as empty values into the wire format.
    assert result["error"]["code"] == "LICENSE_PREFIX_UNSUPPORTED"
    assert "fields" not in result
    assert "source_url" not in result


@pytest.mark.asyncio
async def test_get_package_insert_success_has_null_error(seeded_settings, fixtures_dir):
    """Successful response has `error: null` so callers can branch uniformly."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()
    assert result["error"] is None
    assert result["alternate_versions"] == []  # fixture has 1 insert → no alternates
    assert result["insert_version"] is not None


@pytest.mark.asyncio
async def test_check_insert_updates_batches_date_ranges(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        results = (
            await check_insert_updates(
                since_date="2025-10-01",
                today="2025-10-29",
                settings=seeded_settings,
            )
        ).model_dump()
    # 29-day span → 3 batches of ≤10 days
    assert route.call_count == 3  # noqa: PLR2004
    assert results["error"] is None
    assert results["since_date"] == "2025-10-01"
    assert results["today"] == "2025-10-29"
    assert results["total"] >= 1
    assert any(u["license_no"] == "衛署藥輸字第021571號" for u in results["updates"])
    # by_date is keyed by date string, values are counts.
    assert sum(results["by_date"].values()) == results["total"]
    assert results["batch_errors"] == []


@pytest.mark.asyncio
async def test_check_insert_updates_filters_license_list(seeded_settings, fixtures_dir):
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        results = (
            await check_insert_updates(
                since_date="2025-10-25",
                today="2025-10-29",
                license_list=["衛部藥輸字第026701號"],
                settings=seeded_settings,
            )
        ).model_dump()
    assert results["updates"] == []
    assert results["total"] == 0
    assert results["by_date"] == {}
    assert results["error"] is None


@pytest.mark.asyncio
async def test_check_insert_updates_truncates_at_limit(seeded_settings, monkeypatch):
    """S4.3: `updates` is capped at `limit`; `total` keeps the full count and
    `truncated` flags the cut. `by_date` still summarises ALL updates."""
    many = [
        DrugInsert(license_no=f"L{i}", name_zh=f"藥{i}", name_en=f"D{i}", update_date="2025-10-20")
        for i in range(5)
    ]

    async def _fake_fetch(**_kwargs):
        return many

    monkeypatch.setattr(_tools_mod, "fetch_drug_insert", _fake_fetch)
    result = (
        await check_insert_updates(
            since_date="2025-10-19", today="2025-10-20", limit=2, settings=seeded_settings
        )
    ).model_dump()
    assert result["total"] == 5  # noqa: PLR2004
    assert result["returned"] == 2  # noqa: PLR2004
    assert len(result["updates"]) == 2  # noqa: PLR2004
    assert result["truncated"] is True
    # Histogram reflects every update, not just the returned slice.
    assert sum(result["by_date"].values()) == 5  # noqa: PLR2004


@pytest.mark.asyncio
async def test_check_insert_updates_not_truncated_under_limit(seeded_settings, fixtures_dir):
    """Default limit leaves a small result set untouched: returned == total, truncated False."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await check_insert_updates(
                since_date="2025-10-25", today="2025-10-29", settings=seeded_settings
            )
        ).model_dump()
    assert result["truncated"] is False
    assert result["returned"] == result["total"]


@pytest.mark.asyncio
async def test_get_package_insert_last_update_date_not_duplicated_in_fields(
    seeded_settings, fixtures_dir
):
    """S4.1: last_update_date is top-level metadata only — never duplicated inside `fields`."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(license_no="衛署藥輸字第021571號", settings=seeded_settings)
        ).model_dump(exclude_none=True)
    assert result["last_update_date"] == "2025-10-29"
    assert "last_update_date" not in result["fields"]


@pytest.mark.asyncio
async def test_get_package_insert_explicit_last_update_date_not_flagged_unknown(
    seeded_settings, fixtures_dir
):
    """Explicitly requesting last_update_date is accepted (served top-level), not 'unknown'."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["last_update_date", "indication"],
                settings=seeded_settings,
            )
        ).model_dump(exclude_none=True)
    assert result["last_update_date"] == "2025-10-29"
    assert "last_update_date" not in result["fields"]
    assert "indication" in result["fields"]
    assert "unknown_fields" not in result


@pytest.mark.asyncio
async def test_get_package_insert_surfaces_additional_sections(
    seeded_settings, fixtures_dir
):
    """Text-bearing sections with no named field surface in additional_sections.

    Carries section_no + title + verbatim text, so a future TFDA-added section
    is not silently dropped (the way 1.2 賦形劑 was, before this plan).
    """
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()

    additional = result["additional_sections"]
    # Fixture has section 99 ("未來新欄位") deliberately without a named field.
    assert any(a["section_no"] == "99" for a in additional), additional
    entry_99 = next(a for a in additional if a["section_no"] == "99")
    assert entry_99["title"] == "未來新欄位"
    # The text is carried verbatim (HTML-stripped), not just the section number.
    assert "模擬" in entry_99["text"]
    # Sanity: known sections (e.g. 1.1 ingredients, 2 indication) MUST NOT appear here.
    assert not any(a["section_no"] == "1.1" for a in additional)
    assert not any(a["section_no"] == "2" for a in additional)


@pytest.mark.asyncio
async def test_additional_sections_suppresses_mapped_parent_subsections(
    seeded_settings,
):
    """Sub-sections whose parent section is mapped must NOT appear in additional_sections.

    Example: section 10 'pharmacology' is mapped. The XML walker for that field
    already collects 10.1/10.2/10.3 descendants, so listing 10.1 in
    additional_sections would be a false positive — implying data is missing
    when it is in fact returned via the parent field.
    """
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ROOTDOCUMENT>
  <DOCUMENT>
    <INFO>
      <SNO>衛署藥輸字第000001號</SNO>
      <CNAME>測試藥</CNAME>
      <ENAME>TESTDRUG</ENAME>
      <DTYPE>須由醫師處方使用</DTYPE>
      <SNAME></SNAME>
      <VERSION>1</VERSION>
      <VDATE>2026-01-01</VDATE>
    </INFO>
    <CONTENT>
      <SECTION LEVEL="1" ID="10">
        <NO>10</NO>
        <TITLE>藥理特性</TITLE>
        <SECTION LEVEL="2" ID="10.1">
          <NO>10.1</NO>
          <TITLE>作用機轉</TITLE>
          <VALUE type="text">&lt;p&gt;阻斷鈣離子通道。&lt;/p&gt;</VALUE>
        </SECTION>
        <SECTION LEVEL="2" ID="10.2">
          <NO>10.2</NO>
          <TITLE>藥效藥理特性</TITLE>
          <VALUE type="text">&lt;p&gt;血壓降低。&lt;/p&gt;</VALUE>
        </SECTION>
      </SECTION>
      <SECTION LEVEL="1" ID="99">
        <NO>99</NO>
        <TITLE>未來新欄位</TITLE>
        <VALUE type="text">&lt;p&gt;假章節。&lt;/p&gt;</VALUE>
      </SECTION>
    </CONTENT>
  </DOCUMENT>
</ROOTDOCUMENT>
""".encode()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第000001號",
                settings=seeded_settings,
            )
        ).model_dump()

    additional_numbers = {a["section_no"] for a in result["additional_sections"]}
    # 99 should appear (truly unmapped, no mapped parent).
    assert "99" in additional_numbers, additional_numbers
    # 10.1 and 10.2 must NOT appear (parent 10 is mapped → walker covers them).
    assert "10.1" not in additional_numbers, additional_numbers
    assert "10.2" not in additional_numbers, additional_numbers


_RX_SUBSECTION_EXPECTED = {
    "dosage_general": "3.1",
    "dosage_preparation": "3.2",
    "dosage_special_populations": "3.3",
    "abuse_dependence": "5.2",
    "machine_operation": "5.3",
    "lab_tests": "5.4",
    "other_precautions": "5.5",
    "pregnancy": "6.1",
    "lactation": "6.2",
    "reproductive": "6.3",
    "pediatric": "6.4",
    "geriatric": "6.5",
    "hepatic_impairment": "6.6",
    "renal_impairment": "6.7",
    "other_populations": "6.8",
    "adverse_clinical": "8.1",
    "adverse_trial": "8.2",
    "adverse_postmarketing": "8.3",
    "mechanism_of_action": "10.1",
    "pharmacodynamics": "10.2",
    "nonclinical_safety": "10.3",
}


@pytest.mark.asyncio
async def test_get_package_insert_rx_sub_sections_complete_coverage(
    seeded_settings, fixtures_dir
):
    """All 21 Rx sub-sections (§3.x/5.x/6.x/8.x/10.x) are individually addressable.

    Per 衛福部 110.09.14 公告, each sub-section must be exposable as its own field
    so callers can cite specific numbers (e.g. field_sections['geriatric'] == '6.5').
    """
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=list(_RX_SUBSECTION_EXPECTED),
                settings=seeded_settings,
            )
        ).model_dump()

    fields = result["fields"]
    field_sections = result["field_sections"]
    for f, expected_no in _RX_SUBSECTION_EXPECTED.items():
        assert f in fields, f"missing field {f}"
        assert fields[f], f"field {f} is empty (fixture broken?)"
        assert field_sections.get(f) == expected_no, (
            f"field {f} maps to section {field_sections.get(f)} expected {expected_no}"
        )
    assert result["unknown_fields"] is None


@pytest.mark.asyncio
async def test_special_warning_from_top_level_warning_element(seeded_settings, fixtures_dir):
    """`special_warning` returns the top-level <WARNING> element (加框警語 / BBW)."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["special_warning"],
                settings=seeded_settings,
            )
        ).model_dump()
    assert "心衰竭" in result["fields"]["special_warning"]
    # Non-empty BBW → not flagged as confirmed_absent.
    assert "special_warning" not in result["confirmed_absent"]


@pytest.mark.asyncio
async def test_confirmed_absent_for_empty_characteristics(seeded_settings, fixtures_dir):
    """An always-present field whose XML element is empty → "" in fields + confirmed_absent.

    Distinguishes 'TFDA structurally confirms no 特殊性狀' from 'tool failed'.
    The fixture has no <CHARACT>, so characteristics is structurally absent.
    """
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["characteristics"],
                settings=seeded_settings,
            )
        ).model_dump()
    assert result["fields"]["characteristics"] == ""
    assert "characteristics" in result["confirmed_absent"]


@pytest.mark.asyncio
async def test_format_discriminator_rx(seeded_settings, fixtures_dir):
    """Fixture DTYPE=須由醫師處方使用 → format 'rx'."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()
    assert result["format"] == "rx"


@pytest.mark.asyncio
async def test_images_metadata_present_data_url_gated_on_full(seeded_settings, fixtures_dir):
    """Image metadata always surfaces; data_url only when response_format='full'."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        key_result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()
        full_result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                response_format="full",
                settings=seeded_settings,
            )
        ).model_dump()

    # Metadata present in both; the fixture §1.4 carries one image.
    assert len(key_result["images"]) == 1
    img = key_result["images"][0]
    assert img["section_no"] == "1.4"
    assert img["mime"] == "image/jpeg"
    assert img["size_bytes"] == 6  # noqa: PLR2004
    assert img["data_url"] is None  # not 'full' → no payload

    full_img = full_result["images"][0]
    assert full_img["data_url"] == "data:image/jpeg;base64,QUJDREVG"


@pytest.mark.asyncio
async def test_entity_lists_only_on_full(seeded_settings, fixtures_dir):
    """main_factories / sub_factories / companies surface only on response_format='full'."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        key_result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                settings=seeded_settings,
            )
        ).model_dump()
        full_result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                response_format="full",
                settings=seeded_settings,
            )
        ).model_dump()

    assert key_result["main_factories"] == []
    assert key_result["companies"] == []

    assert len(full_result["main_factories"]) == 1
    assert full_result["main_factories"][0]["name"] == "久裕企業股份有限公司"
    assert full_result["main_factories"][0]["number"] == "1"
    assert len(full_result["sub_factories"]) == 1
    assert len(full_result["companies"]) == 2  # noqa: PLR2004
    assert full_result["companies"][0]["name"] == "暉致醫藥股份有限公司"


# --- OTC dispatch + title-folding fidelity (Phase 3.2, ADR-0007 Strategy B) ----

_OTC_LICENSE = "衛署成製字第007884號"  # 安皮露 (ONPYLU)


async def _fetch_otc(seeded_settings, fixtures_dir, **kwargs):
    xml = (fixtures_dir / "getdrugdoc_otc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        return (
            await get_package_insert(license_no=_OTC_LICENSE, settings=seeded_settings, **kwargs)
        ).model_dump()


@pytest.mark.asyncio
async def test_otc_dispatch_and_coverage(seeded_settings, fixtures_dir):
    """安皮露 (DTYPE=成藥) dispatches to the OTC field space, not the Rx one."""
    result = await _fetch_otc(seeded_settings, fixtures_dir, fields="all")
    assert result["format"] == "otc"
    fields = result["fields"]
    # OTC field names present and populated:
    assert "香港腳" in fields["usage"]  # §2 用途(適應症)
    assert "Salicylic" in fields["ingredients"]  # §1.1
    assert "敷於患部" in fields["directions"]  # §4 用法用量
    assert result["field_sections"]["usage"] == "2"
    assert result["field_sections"]["directions"] == "4"
    # Rx-only field names MUST NOT appear in an OTC response:
    for rx_only in ("indication", "dosage", "warnings", "side_effects", "pharmacology"):
        assert rx_only not in fields, rx_only


@pytest.mark.asyncio
async def test_otc_title_borne_content_folds_into_stable_parents(seeded_settings, fixtures_dir):
    """OTC §3/§5 content lives in nested <TITLE>s — folded into the stable parents.

    §3.x / §5.x sub-numbering varies per drug (live-verified 2026-05-30), so only
    the parents usage_precautions / otc_warnings are named; title-folding keeps
    every sub-item's safety-critical text inside them.
    """
    result = await _fetch_otc(seeded_settings, fixtures_dir, fields="all")
    fields = result["fields"]
    # §3 parent folds 3.1 請勿使用 (過敏) + 3.4 其他 (外用-not-internal warning).
    assert "過敏" in fields["usage_precautions"]
    assert "不得內服" in fields["usage_precautions"]
    assert result["field_sections"]["usage_precautions"] == "3"
    # §5 parent folds the §5.1 副作用 table (紅斑) + §5.2 症狀 anaphylaxis red flags.
    assert "紅斑" in fields["otc_warnings"]
    assert "呼吸困難" in fields["otc_warnings"]
    assert result["field_sections"]["otc_warnings"] == "5"
    # Brittle per-number sub-fields are NOT exposed (they mislabel content across drugs).
    for dropped in ("do_not_use", "consult_doctor_before_use", "adverse_warning", "symptom_warning"):
        assert dropped not in fields


@pytest.mark.asyncio
async def test_otc_characteristics_shared_special_warning_invalid(seeded_settings, fixtures_dir):
    """characteristics (<CHARACT>) is shared; special_warning is Rx-only (no OTC BBW)."""
    result = await _fetch_otc(seeded_settings, fixtures_dir, fields=["characteristics", "special_warning"])
    assert "殺菌" in result["fields"]["characteristics"]
    assert "characteristics" not in result["confirmed_absent"]  # populated
    # special_warning is not a valid OTC field → surfaced as unknown, not silently dropped.
    assert result["unknown_fields"] is not None
    assert any(u["input"] == "special_warning" for u in result["unknown_fields"])


@pytest.mark.asyncio
async def test_get_package_insert_returns_available_sections_toc(seeded_settings, fixtures_dir):
    """Every response carries a TOC of every populated section, regardless of `fields`."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        result = (
            await get_package_insert(
                license_no="衛署藥輸字第021571號",
                fields=["indication"],  # narrow request — TOC must still list everything
                settings=seeded_settings,
            )
        ).model_dump()

    toc = result["available_sections"]
    assert isinstance(toc, list)
    assert len(toc) > 5  # noqa: PLR2004
    for entry in toc:
        assert set(entry) >= {"section_no", "title", "char_count", "field_name"}
        assert entry["char_count"] >= 0

    indication_entry = next(e for e in toc if e["section_no"] == "2")
    assert indication_entry["title"] == "適應症"
    assert indication_entry["field_name"] == "indication"
    assert indication_entry["char_count"] > 0

    # A section NOT requested but present must still appear, tagged with its field name.
    pediatric_entry = next((e for e in toc if e["section_no"] == "6.4"), None)
    assert pediatric_entry is not None
    assert pediatric_entry["field_name"] == "pediatric"

    # Unmapped section 99 appears with field_name=None.
    section99 = next((e for e in toc if e["section_no"] == "99"), None)
    assert section99 is not None
    assert section99["field_name"] is None


@pytest.mark.asyncio
async def test_otc_toc_lists_stable_parent_sections(seeded_settings, fixtures_dir):
    """OTC TOC lists the stable §3 使用上注意事項 parent (folded title content counted)."""
    result = await _fetch_otc(seeded_settings, fixtures_dir)
    toc = {e["section_no"]: e for e in result["available_sections"]}
    assert toc["2"]["field_name"] == "usage"
    assert toc["3"]["field_name"] == "usage_precautions"
    assert toc["3"]["char_count"] > 0  # folded §3 sub-item titles counted
    # §3.x sub-sections are not named fields; title-only ones are not listed individually.
    assert "3.1.1" not in toc
    assert toc.get("3.1", {}).get("field_name") is None


# --- SWR refresh (stale-while-revalidate, ADR-0009) ---------------------------


@pytest.mark.asyncio
async def test_stale_memo_serves_stale_and_schedules_refresh(monkeypatch, tmp_path):
    """A stale memo is returned immediately AND a background refresh is scheduled."""
    fetched = {"n": 0}

    async def fake_fetch(base_url, **_kwargs):
        fetched["n"] += 1
        return []

    monkeypatch.setattr(_tools_mod, "fetch_dataset37", fake_fetch)
    s = make_settings(cache_dir=tmp_path, ttl_hours=24)
    _tools_mod._LICENSES_CACHE = ["stale"]
    _tools_mod._LICENSES_LOADED_AT = 0.0  # epoch 1970 → unambiguously stale
    _tools_mod._REFRESH_TASK = None

    out = await _tools_mod._load_or_refresh_licenses(s)

    assert out == ["stale"]  # served stale immediately — did NOT block on the fetch
    assert _tools_mod._REFRESH_TASK is not None  # background refresh was scheduled
    await _tools_mod._REFRESH_TASK
    assert fetched["n"] == 1


@pytest.mark.asyncio
async def test_single_inflight_refresh_guard(monkeypatch, tmp_path):
    """Two stale calls while a refresh is running spawn only ONE download."""
    started = {"n": 0}

    async def slow_fetch(base_url, **_kwargs):
        started["n"] += 1
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(_tools_mod, "fetch_dataset37", slow_fetch)
    s = make_settings(cache_dir=tmp_path, ttl_hours=24)
    _tools_mod._LICENSES_CACHE = ["stale"]
    _tools_mod._LICENSES_LOADED_AT = 0.0
    _tools_mod._REFRESH_TASK = None

    await _tools_mod._load_or_refresh_licenses(s)
    await _tools_mod._load_or_refresh_licenses(s)  # second stale call, first still running
    await _tools_mod._REFRESH_TASK
    assert started["n"] == 1  # guard prevented a duplicate concurrent download


@pytest.mark.asyncio
async def test_background_failure_keeps_stale(monkeypatch, tmp_path):
    """A failed background refresh leaves the stale memo intact and never raises."""

    async def boom(*a, **k):
        raise DatasetFetchError(RCode.DATASET_FETCH_FAILED, "down")

    monkeypatch.setattr(_tools_mod, "fetch_dataset37", boom)
    s = make_settings(cache_dir=tmp_path, ttl_hours=0)  # always stale
    _tools_mod._LICENSES_CACHE = ["old"]
    _tools_mod._LICENSES_LOADED_AT = 0.0
    _tools_mod._REFRESH_TASK = None

    out = await _tools_mod._load_or_refresh_licenses(s)

    assert out == ["old"]  # served stale, no raise
    await _tools_mod._REFRESH_TASK
    assert _tools_mod._LICENSES_CACHE == ["old"]  # failed refresh kept the stale memo


@pytest.mark.asyncio
async def test_cold_start_stale_disk_serves_then_refreshes(monkeypatch, tmp_path):
    """Cold start with a stale on-disk cache serves it and schedules a refresh."""
    fetched = {"n": 0}

    async def fake_fetch(base_url, **_kwargs):
        fetched["n"] += 1
        return []

    monkeypatch.setattr(_tools_mod, "fetch_dataset37", fake_fetch)
    s = make_settings(cache_dir=tmp_path, ttl_hours=24)
    # Seed a cache file and backdate its mtime so it reads as stale.
    write_to_cache([], tmp_path)
    stale_path = tmp_path / "dataset37.json"
    old = time.time() - 48 * 3600
    os.utime(stale_path, (old, old))

    out = await _tools_mod._load_or_refresh_licenses(s)  # cold start (memo is None)

    assert out == []  # served disk cache immediately
    assert _tools_mod._REFRESH_TASK is not None  # stale disk → refresh scheduled
    await _tools_mod._REFRESH_TASK
    assert fetched["n"] == 1


@pytest.mark.asyncio
async def test_search_response_carries_freshness(seeded_settings):
    """search_drugs surfaces explicit dataset freshness so the LLM can judge staleness."""
    resp = await search_drugs(query="脈優", settings=seeded_settings)
    assert resp.dataset_retrieved_at is not None
    datetime.fromisoformat(resp.dataset_retrieved_at)  # valid ISO 8601
    assert isinstance(resp.dataset_age_hours, float)
    assert resp.dataset_age_hours >= 0
    assert resp.is_stale is False  # freshly seeded cache is within TTL


@pytest.mark.asyncio
async def test_search_response_is_stale_when_serving_stale(monkeypatch, tmp_path):
    """When the served index is past TTL (refresh pending/failed), is_stale is True."""

    async def boom(base_url, **_kwargs):
        raise DatasetFetchError(RCode.DATASET_FETCH_FAILED, "down")

    monkeypatch.setattr(_tools_mod, "fetch_dataset37", boom)
    s = make_settings(cache_dir=tmp_path, ttl_hours=0)  # any age reads as stale
    _tools_mod._LICENSES_CACHE = []
    _tools_mod._LICENSES_LOADED_AT = 0.0

    resp = await search_drugs(query="脈優", settings=s)

    assert resp.is_stale is True
    assert resp.dataset_age_hours is not None
    assert resp.dataset_age_hours > 0
    if _tools_mod._REFRESH_TASK is not None:  # drain the scheduled background refresh
        await _tools_mod._REFRESH_TASK


@pytest.mark.asyncio
async def test_search_no_criteria_returns_error(seeded_settings):
    """No criteria → explicit SEARCH_NO_CRITERIA error, not a silent empty success."""
    resp = (await search_drugs(settings=seeded_settings)).model_dump()
    assert resp["error"]["code"] == "SEARCH_NO_CRITERIA"
    assert resp["total_matched"] == 0


@pytest.mark.asyncio
async def test_search_whitespace_only_is_no_criteria_error(seeded_settings):
    """Whitespace-only input is treated the same as no input (guard strips first)."""
    resp = (await search_drugs(query="   ", name_zh="  ", settings=seeded_settings)).model_dump()
    assert resp["error"]["code"] == "SEARCH_NO_CRITERIA"
    assert resp["total_matched"] == 0


@pytest.mark.asyncio
async def test_get_package_insert_configures_throttle_from_settings(
    seeded_settings, fixtures_dir: Path
):
    """tools.get_package_insert must push the configured interval onto the
    shared egress throttle so the gate is actually armed in Model B."""
    get_insert_throttle().min_interval = 0.0  # ensure pre-state is 0 before test

    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    settings = Settings(  # type: ignore[call-arg]
        DATASET37_CACHE_DIR=seeded_settings.DATASET37_CACHE_DIR,
        DATASET37_TTL_HOURS=24,
        FDA_RATE_LIMIT_INTERVAL_SECONDS=0.0,
        FDA_INSERT_BASE_URL="https://mcp.fda.gov.tw",
        INSERT_THROTTLE_MIN_INTERVAL_SECONDS=0.7,
    )
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        await get_package_insert("衛署藥輸字第021571號", settings=settings)
    assert get_insert_throttle().min_interval == 0.7  # noqa: PLR2004


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_refresh():
    async def never_finishes(*a, **k):
        await asyncio.sleep(3600)
        return []

    _tools_mod._REFRESH_TASK = asyncio.create_task(never_finishes())
    await asyncio.sleep(0)  # let it start
    await _tools_mod.shutdown()
    assert _tools_mod._REFRESH_TASK is None  # cancelled and cleared, no raise


@pytest.mark.asyncio
async def test_get_package_insert_cache_hit_skips_refetch(seeded_settings, fixtures_dir):
    """With the cache on, a repeat lookup is served from memory (one network call)."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    settings = seeded_settings.model_copy(update={"INSERT_CACHE_ENABLED": True})
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        first = await get_package_insert(license_no="衛署藥輸字第021571號", settings=settings)
        second = await get_package_insert(license_no="衛署藥輸字第021571號", settings=settings)

    assert route.call_count == 1  # second served from cache
    assert first.from_cache is False
    assert first.cache_age_hours is None
    assert second.from_cache is True
    assert second.cache_age_hours is not None
    assert second.cache_age_hours >= 0
    assert first.fields == second.fields  # identical content on a hit
    assert first.last_update_date == second.last_update_date  # citation currency unaffected


@pytest.mark.asyncio
async def test_get_package_insert_cache_disabled_refetches(seeded_settings, fixtures_dir):
    """Default (cache off): every call fetches live (ADR-0009 behaviour preserved)."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        route = router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        r1 = await get_package_insert(license_no="衛署藥輸字第021571號", settings=seeded_settings)
        r2 = await get_package_insert(license_no="衛署藥輸字第021571號", settings=seeded_settings)

    assert route.call_count == 2  # noqa: PLR2004
    assert r1.from_cache is False
    assert r2.from_cache is False
    assert r1.cache_age_hours is None
    assert r2.cache_age_hours is None


@pytest.mark.asyncio
async def test_check_insert_updates_bypasses_insert_cache(seeded_settings, fixtures_dir):
    """check_insert_updates must neither read nor write the insert cache (ADR-0011 §6)."""
    xml = (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()
    settings = seeded_settings.model_copy(update={"INSERT_CACHE_ENABLED": True})
    cache = get_insert_cache()
    async with respx.mock(base_url="https://mcp.fda.gov.tw") as router:
        router.get("/Serv/Query.asmx/GetDrugDoc").mock(
            return_value=httpx.Response(200, content=xml)
        )
        await check_insert_updates("2025-10-20", today="2025-10-29", settings=settings)

    assert len(cache._store) == 0  # the date-sweep path never populated the cache
