# path: src/taiwan_fda_mcp/mcp_server.py
# brief: FastMCP stdio server exposing taiwan_fda_mcp.tools as MCP tools.

from typing import Any, Literal

from fastmcp import FastMCP

from taiwan_fda_mcp.config import get_settings
from taiwan_fda_mcp.logging_config import configure_logging
from taiwan_fda_mcp.tools import (
    check_insert_updates as _check_insert_updates,
)
from taiwan_fda_mcp.tools import (
    get_package_insert as _get_package_insert,
)
from taiwan_fda_mcp.tools import (
    search_drugs as _search_drugs,
)

SearchByLiteral = Literal["any", "name_zh", "name_en", "ingredient", "license_no"]
FieldGroupLiteral = Literal["all", "key_fields"]

mcp: FastMCP = FastMCP(
    name="taiwan-fda-mcp",
    instructions=(
        "查詢台灣食藥署 (TFDA) 維護的官方藥物資訊：藥品許可證、仿單章節、更新追蹤。"  # noqa: RUF001
        "Sources: data.fda.gov.tw (Dataset 37) + mcp.fda.gov.tw (GetDrugDoc XML API)."
    ),
)


@mcp.tool
async def search_drugs(
    query: str,
    search_by: SearchByLiteral = "any",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search Taiwan FDA drug licenses by Chinese / English name, active ingredient, or license number.

    Args:
        query: keyword to search (e.g. "脈優", "atorvastatin", "021571").
        search_by: which field to search. "any" (default) searches name + ingredient + license.
        limit: maximum results (default 10).

    Returns:
        List of drug license rows with license_no, name_zh, name_en, ingredient, form,
        manufacturer, applicant, drug_class, status. All rows are active (Dataset 37 = 未註銷).
    """
    return await _search_drugs(query=query, search_by=search_by, limit=limit)


@mcp.tool
async def get_package_insert(
    license_no: str,
    fields: FieldGroupLiteral | list[str] = "key_fields",
) -> dict[str, Any]:
    """Fetch the official package insert (仿單) for a Taiwan FDA drug license.

    Args:
        license_no: full Chinese license string (e.g. "衛署藥輸字第021571號").
        fields: which fields to extract. Either "key_fields" (default — indication, dosage,
            contraindications, warnings, side_effects, last_update_date), "all" (every
            available field), or an explicit list of field names from this exact set:
            Basic — name_zh, name_en, license_no, form, applicant, manufacturer,
            drug_class, valid_until;
            Clinical — indication, dosage, contraindications, warnings, interactions,
            side_effects;
            Pharmacology — ingredients, pharmacology, pharmacokinetics;
            Storage — packaging, storage_conditions;
            Metadata — last_update_date, insert_version.
            Unknown names are returned in `unknown_fields` for self-correction.

    Returns:
        Dict with license_no, fields (text per field), source_url, retrieved_at, last_update_date.
        On unsupported license prefix or fetch failure, returns {"license_no": ..., "error": {...}}.
    """
    return await _get_package_insert(license_no=license_no, fields=fields)


@mcp.tool
async def check_insert_updates(
    since_date: str,
    license_list: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List Taiwan FDA drug inserts that were updated on or after the given date.

    Args:
        since_date: 'YYYY-MM-DD' — lower bound (inclusive).
        license_list: optional. If provided, only inserts whose license_no is in this list are returned.

    Returns:
        List of {license_no, name_zh, last_update_date, has_updated} dicts.
        The GetDrugDoc API caps each request at a 10-day window — this tool batches automatically.
    """
    return await _check_insert_updates(since_date=since_date, license_list=license_list)


def main() -> None:
    """Console-script entry point — starts the stdio MCP server."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    mcp.run()


if __name__ == "__main__":
    main()
