# path: tests/unit/test_tools.py
# brief: Verify tools.py entry-point behaviour.

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

import taiwan_fda_mcp.tools as _tools_mod
from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows, write_to_cache
from taiwan_fda_mcp.tools import (
    check_insert_updates,
    get_package_insert,
    search_drugs,
)


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Clear tools.py module-level Dataset 37 memo between tests."""
    _tools_mod._LICENSES_CACHE = None


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
    )


@pytest.mark.asyncio
async def test_search_drugs_returns_envelope_with_total(seeded_settings):
    response = (await search_drugs(query="脈優", settings=seeded_settings)).model_dump()
    assert response["query"] == "脈優"
    assert response["search_by"] == "any"
    assert response["error"] is None
    assert response["total_matched"] == 1
    assert response["returned"] == 1
    assert response["truncated"] is False
    row = response["results"][0]
    assert row["license_no"] == "衛署藥輸字第021571號"
    assert row["name_zh"] == "脈優錠５毫克"


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
    assert "warnings" in fields
    assert "心衰竭" in fields["warnings"]
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
    assert "孕婦" in fields["special_populations"]
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
async def test_get_package_insert_surfaces_unmapped_sections(
    seeded_settings, fixtures_dir
):
    """Sections present in XML but not in _SECTION_NUMBERS surface as unmapped_sections.

    Safety net so a future TFDA-added section is not silently dropped (the way
    1.2 賦形劑 was, before this plan).
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

    unmapped = result["unmapped_sections"]
    # Fixture has section 99 ("未來新欄位") deliberately not in _SECTION_NUMBERS.
    assert any(u["section_no"] == "99" for u in unmapped), unmapped
    entry_99 = next(u for u in unmapped if u["section_no"] == "99")
    assert entry_99["title"] == "未來新欄位"
    # Sanity: known sections (e.g. 1.1 ingredients) MUST NOT appear here.
    assert not any(u["section_no"] == "1.1" for u in unmapped)
    assert not any(u["section_no"] == "2" for u in unmapped)
