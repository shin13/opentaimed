# path: tests/integration/test_live_smoke.py
# brief: Live real-API smoke tests guarding the TFDA contract against silent drift.

"""Live smoke tests against the real Taiwan FDA APIs.

These hit `data.fda.gov.tw` (Dataset 37) and `mcp.fda.gov.tw` (GetDrugDoc)
for real, so they are marked `integration` + `smoke` and are EXCLUDED from
the default `pytest` run (`addopts = -m 'not integration'`). Run explicitly:

    uv run pytest -m smoke

Why these exist: FDA contract drift (a renamed field, a dropped section, a
changed license number) propagates into LLM citations within hours. A daily
CI run of these guards against that silently happening.

Each case below was live-verified on 2026-06-02; the exact license numbers
are pinned so a regression points at a specific, reproducible drug.
"""

from datetime import UTC, datetime, timedelta

import pytest

# NOTE: `taiwan_fda_mcp.tools` is imported lazily inside each test, NOT at module
# top level. Importing it at collection time warms Pydantic's schema cache in a
# different order and reorders an `anyOf` union in the get_package_insert input
# schema, breaking tests/unit/test_mcp_schemas.py. Keeping the import local to the
# tests (which only run under `-m smoke`/`integration`) avoids that collection-time
# side effect entirely.

# Live-verified reference licenses (2026-06-02).
NORVASC = "衛署藥輸字第021571號"  # 脈優錠５毫克 — Rx, amlodipine
HERCEPTIN_150 = "衛署菌疫輸字第000790號"  # 賀癌平凍晶注射劑150mg — Rx, has 加框警語
PANADOL_500 = "衛署藥輸字第023624號"  # 普拿疼膜衣錠500mg — 指示藥 OTC

pytestmark = [pytest.mark.integration, pytest.mark.smoke]


async def test_search_finds_norvasc_by_exact_name():
    """`search_drugs(name_zh="脈優錠")` resolves to the pinned Norvasc license.

    Use the exact name `脈優錠`, NOT substring `脈優`: the latter also matches
    脂脈優 (Caduet, an amlodipine+atorvastatin combo) and would be ambiguous.
    """
    from taiwan_fda_mcp.tools import search_drugs

    resp = await search_drugs(name_zh="脈優錠")
    assert resp.error is None
    license_nos = {row.license_no for row in resp.results}
    assert NORVASC in license_nos, f"脈優錠 missing; got {license_nos}"


async def test_norvasc_insert_has_contraindications_section_4():
    """Citation traceability: contraindications must map to insert section 4."""
    from taiwan_fda_mcp.tools import get_package_insert

    resp = await get_package_insert(NORVASC, response_format="key")
    assert resp.error is None
    assert resp.format == "rx"
    assert resp.fields.get("contraindications", "").strip(), "contraindications empty"
    assert resp.field_sections.get("contraindications") == "4"


async def test_herceptin_has_black_box_special_warning():
    """加框警語 regression guard — 賀癌平 must surface a non-empty special_warning.

    The <WARNING> element → special_warning mapping (ADR-0007) is the strongest
    clinical safety signal. If this drug ever returns empty here, the mapping
    has silently broken.
    """
    from taiwan_fda_mcp.tools import get_package_insert

    resp = await get_package_insert(HERCEPTIN_150, response_format="concise")
    assert resp.error is None
    special_warning = resp.fields.get("special_warning", "")
    assert "心肌病變" in special_warning, f"加框警語 missing: {special_warning!r}"
    # Non-empty warning means it is NOT in confirmed_absent.
    assert "special_warning" not in resp.confirmed_absent


async def test_panadol_designated_otc_routes_to_otc_format():
    """指示藥 OTC dispatch guard — 普拿疼 (DTYPE=指示藥品) must classify as OTC.

    This drug's license 字軌 is the Rx-import `衛署藥輸字`, yet it is a designated
    OTC. Format dispatch must key on <DTYPE>, never the license prefix.
    """
    from taiwan_fda_mcp.tools import get_package_insert

    resp = await get_package_insert(PANADOL_500)
    assert resp.error is None
    assert resp.format == "otc", f"指示藥 misclassified as {resp.format}"
    assert resp.fields.get("otc_warnings", "").strip(), "otc_warnings empty"


async def test_check_insert_updates_returns_recent_activity():
    """Recent-updates feed must be non-empty and carry a per-date histogram."""
    from taiwan_fda_mcp.tools import check_insert_updates

    since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
    resp = await check_insert_updates(since)
    assert resp.error is None
    assert resp.total >= 1, f"no updates since {since}"
    assert resp.by_date, "by_date histogram empty"


@pytest.mark.parametrize(
    "license_no",
    [NORVASC, HERCEPTIN_150, PANADOL_500],
)
async def test_every_insert_carries_attribution(license_no: str):
    """P1.2 guard — every insert response must carry the non-official wrapper disavowal."""
    from taiwan_fda_mcp.tools import get_package_insert

    resp = await get_package_insert(license_no, response_format="concise")
    assert resp.error is None
    assert resp.attribution is not None
    assert resp.attribution.wrapper.strip(), "attribution.wrapper empty"
    assert resp.attribution.data_official is True
