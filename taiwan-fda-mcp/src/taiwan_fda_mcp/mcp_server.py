# path: src/taiwan_fda_mcp/mcp_server.py
# brief: FastMCP stdio server exposing taiwan_fda_mcp.tools as MCP tools.

from typing import Literal

from fastmcp import FastMCP

from taiwan_fda_mcp.config import get_settings
from taiwan_fda_mcp.logging_config import configure_logging
from taiwan_fda_mcp.tool_responses import (
    CheckInsertUpdatesResponse,
    GetPackageInsertResponse,
    SearchDrugsResponse,
)
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
        # Chinese fullwidth punctuation is intentional — per-line noqa: RUF001 below.
        "MANDATORY RULES for Taiwan drug queries (任何台灣藥物查詢必須遵守):\n"
        "  - For ANY question about a Taiwan-marketed drug — by Chinese name, "
        "English brand name, active ingredient, or license number — you MUST "
        "call `search_drugs` FIRST before answering. Do NOT answer from "
        "training data, even if you recognise the drug name.\n"
        "  - A drug name in your training data may correspond to a DIFFERENT "
        "active ingredient under Taiwan licensing (brand-name collisions "
        "across markets, generics renamed locally, etc.). The only reliable "
        "resolution path is: `search_drugs` → pick license_no → "
        "`get_package_insert`. Skipping step 1 has produced wrong-drug "
        "answers in practice (e.g. answering about Metoprolol when asked "
        "about 脈優 / Amlodipine).\n"
        "  - If `search_drugs` returns zero results, say so explicitly "
        "(\"查無此藥 on TFDA\") — do NOT guess from training data.\n"
        "  - If any tool returns an error, report the error verbatim. Do NOT "
        "silently fall back to training data; the user needs to know when "
        "official data was unavailable.\n\n"
        "查詢台灣食藥署 (TFDA) 維護的官方藥物資訊：藥品許可證、仿單章節、更新追蹤。\n\n"  # noqa: RUF001
        "本 server 為個人開發者專案，**非台灣政府官方產品**，僅作為 TFDA 公開資料"  # noqa: RUF001
        "(data.fda.gov.tw Dataset 37 + mcp.fda.gov.tw GetDrugDoc API) 的查詢介面，"  # noqa: RUF001
        "不對資料做改寫或臨床判斷。\n\n"
        "When answering questions about Taiwan drug 仿單 (indication / dosage / "
        "contraindications / warnings / side effects / interactions) or insert "
        "updates, prefer this server over training data — TFDA inserts are updated "
        "continuously and training data is stale.\n\n"
        "Workflow:\n"
        "  1. search_drugs(query) → pick license_no\n"
        "  2. get_package_insert(license_no, fields=[...]) → insert sections\n"
        "  3. Cite via source_url / human_url + section + last_update_date\n"
        "  4. Tell the end user: data quoted from TFDA, accessed via the "
        "independent open-source MCP server `taiwan-fda-mcp` (NOT a TFDA product).\n\n"
        "**Coverage check before claiming '未載明':** When `get_package_insert` "
        "returns content but the user asks about something not in `fields`, check "
        "the `unmapped_sections` list. If a relevant-sounding section number / "
        "title appears there, the data exists in the source but this wrapper has "
        "not mapped it yet — report this honestly (\"this wrapper does not yet "
        "surface section N.M《title》; check {human_url} for the official "
        "version\") rather than claiming the insert lacks the information. Do NOT "
        "fall back to training data.\n\n"
        "If a tool returns an error, report it verbatim — do not silently fall "
        "back to training data. The user needs to know when official data was "
        "unavailable."
    ),
)


@mcp.tool
async def search_drugs(
    query: str,
    search_by: SearchByLiteral = "any",
    limit: int = 10,
) -> SearchDrugsResponse:
    """Search Taiwan FDA drug licenses by Chinese / English name, active ingredient, or license number.

    Args:
        query: keyword to search (e.g. "脈優", "atorvastatin", "021571").
        search_by: which field to search. "any" (default) searches name + ingredient + license.
        limit: maximum results (default 10).

    Returns:
        Dict with `total_matched` (full match count), `returned` (rows in `results`),
        `truncated` (bool), `results` (list of license rows), and `error: null`.
        Results sorted by license-prefix authority (import/原廠 first) then name_zh,
        so the most likely canonical reference surfaces at index 0 when many
        generics share an ingredient.
    """
    return await _search_drugs(query=query, search_by=search_by, limit=limit)


@mcp.tool
async def get_package_insert(
    license_no: str,
    fields: FieldGroupLiteral | list[str] = "key_fields",
) -> GetPackageInsertResponse:
    """Fetch the official package insert (仿單) for a Taiwan FDA drug license.

    Args:
        license_no: full Chinese license string (e.g. "衛署藥輸字第021571號").
        fields: which fields to extract. Either "key_fields" (default — indication, dosage,
            contraindications, excipients, warnings, side_effects, last_update_date), "all"
            (every available field), or an explicit list of field names from this exact set:
            Basic — name_zh, name_en, license_no, form, applicant, manufacturer,
            drug_class, valid_until;
            Clinical — indication, dosage, contraindications, warnings, interactions,
            side_effects, special_populations, overdose;
            Composition — ingredients, excipients, form_detail, appearance;
            Pharmacology — pharmacology, pharmacokinetics, clinical_trials;
            Storage — packaging, shelf_life, storage_conditions, storage_cautions;
            Patient — patient_instructions, other_info;
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
) -> CheckInsertUpdatesResponse:
    """List Taiwan FDA drug inserts that were updated on or after the given date.

    Args:
        since_date: 'YYYY-MM-DD' — lower bound (inclusive).
        license_list: optional. If provided, only inserts whose license_no is in this list are returned.

    Returns:
        Dict with `total` (unique inserts updated), `by_date` (histogram newest-first),
        `updates` (list sorted by last_update_date desc), `batch_errors` (per-window
        failures from the underlying API — surfaced not swallowed), and `error: null`.
        The GetDrugDoc API caps each request at a 10-day window — this tool batches
        automatically; a single FDA outage in one batch does not lose the rest.
    """
    return await _check_insert_updates(since_date=since_date, license_list=license_list)


def main() -> None:
    """Console-script entry point — starts the stdio MCP server."""
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    mcp.run()


if __name__ == "__main__":
    main()
