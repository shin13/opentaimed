# path: src/taiwan_fda_mcp/mcp_server.py
# brief: FastMCP stdio server exposing taiwan_fda_mcp.tools as MCP tools.

from contextlib import asynccontextmanager
from typing import Literal

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from taiwan_fda_mcp.config import get_settings
from taiwan_fda_mcp.logging_config import configure_logging
from taiwan_fda_mcp.resources import OTC_INSERT_STRUCTURE_MD, RX_INSERT_STRUCTURE_MD
from taiwan_fda_mcp.tool_responses import (
    CheckInsertUpdatesResponse,
    GetPackageInsertResponse,
    SearchByIngredientResponse,
    SearchDrugsResponse,
)
from taiwan_fda_mcp.tools import (
    check_insert_updates as _check_insert_updates,
)
from taiwan_fda_mcp.tools import (
    get_package_insert as _get_package_insert,
)
from taiwan_fda_mcp.tools import (
    search_by_ingredient as _search_by_ingredient,
)
from taiwan_fda_mcp.tools import (
    search_drugs as _search_drugs,
)
from taiwan_fda_mcp.tools import shutdown as _shutdown_refresh

FieldGroupLiteral = Literal["all", "key_fields"]
ResponseFormatLiteral = Literal["concise", "key", "detailed", "full"]


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    """Cancel the background refresh task on graceful shutdown (ADR-0010)."""
    yield
    await _shutdown_refresh()


mcp: FastMCP = FastMCP(
    name="taiwan-fda-mcp",
    lifespan=_lifespan,
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
        "**Ingredient-scoped lookup:** When the user asks which products contain "
        "an active ingredient, or wants single-ingredient (單方) vs combination "
        "(複方) products for one ingredient, call `search_by_ingredient(ingredient)` "
        "instead of search_drugs — it groups licenses by 主成分略述. Salt-form "
        "spellings (BESYLATE vs BESILATE) form distinct groups by design; do NOT "
        "tell the user they are the same drug — report the groups as returned. Then "
        "pick a license_no from a group and continue with get_package_insert.\n\n"
        "**Black box warning (special_warning):** When this field is non-empty, "
        "you MUST quote its content verbatim in any response that mentions "
        "warnings or contraindications. Do NOT paraphrase, summarise, or merge "
        "it with the §5 warnings. TFDA 加框警語 is the strongest clinical safety "
        "signal — losing wording is a medical safety risk. When `special_warning` "
        "is empty AND its name appears in `confirmed_absent`, you MAY state "
        "\"TFDA 仿單確認此藥無加框警語\" as a positive clinical fact (this is "
        "DIFFERENT from \"資料庫查無加框警語\", which would imply tool failure).\n\n"
        "**Coverage check before claiming '未載明':** When `get_package_insert` "
        "returns content but the user asks about something not in `fields`, check "
        "the `additional_sections` list. Each entry carries the section number, "
        "title, AND verbatim text — quote it and cite the section_no directly. If "
        "the topic is in neither `fields` nor `additional_sections`, say the "
        "insert does not document it; direct the user to `human_url` for the "
        "official page. Do NOT fall back to training data.\n\n"
        "**`available_sections` table of contents:** Every successful response "
        "includes a flat list of every populated section in this drug's insert — "
        "section number, title, char count, and the wrapper field name (or null "
        "if unmapped). Use it to (a) see what else is in this insert beyond what "
        "you requested in `fields`, (b) cite section numbers precisely, (c) decide "
        "whether a second call for specific fields is worthwhile. NEVER assume the "
        "`fields` dict is exhaustive — check the TOC first.\n\n"
        "**Reference resources available:**\n"
        "  - `structure://rx-insert` — TFDA 處方藥仿單 official structure "
        "(15 sections + sub-sections + field-name map)\n"
        "  - `structure://otc-insert` — TFDA 非處方藥仿單 official structure "
        "(6 sections + field-name map)\n"
        "Read these to learn which field name maps to which TFDA section — useful "
        "for precise citations (§6.5 老年人 → field `geriatric`), planning "
        "multi-section queries, or judging whether a piece of information is even "
        "within this insert format's scope.\n\n"
        "If a tool returns an error, report it verbatim — do not silently fall "
        "back to training data. The user needs to know when official data was "
        "unavailable."
    ),
)


@mcp.tool
async def search_drugs(
    query: str = "",
    name_zh: str = "",
    name_en: str = "",
    ingredient: str = "",
    indication: str = "",
    applicant: str = "",
    manufacturer: str = "",
    form: str = "",
    drug_class: str = "",
    country: str = "",
    limit: int = 10,
) -> SearchDrugsResponse:
    """Search Taiwan FDA drug licenses by any combination of fields (AND-combined).

    All parameters are optional; provide at least one. Free-text fields match by
    case-insensitive substring; `country` matches by case-insensitive exact code.

    Args:
        query: fuzzy keyword across Chinese/English name + ingredient + license_no
            (e.g. "脈優", "atorvastatin", "021571").
        name_zh: 中文品名 substring (e.g. "脈優").
        name_en: 英文品名 substring (e.g. "norvasc").
        ingredient: 主成分 substring (e.g. "amlodipine").
        indication: 適應症 substring (e.g. "高血壓").
        applicant: 申請商 substring.
        manufacturer: 製造商 substring (matches if ANY manufacturer of a license matches).
        form: 劑型 substring (e.g. "錠劑").
        drug_class: 藥品類別 substring.
        country: 製造廠國別 — EXACT, case-insensitive (e.g. "TW").
        limit: maximum results (default 10).

    Returns:
        SearchDrugsResponse — `total_matched` (full distinct-license count),
        `returned`, `truncated`, `results` (license rows with a `manufacturers`
        list), dataset freshness fields, and `error` (set when no criteria given).
        Results sorted by license-prefix authority (import/原廠 first) then name_zh.
    """
    return await _search_drugs(
        query=query,
        name_zh=name_zh,
        name_en=name_en,
        ingredient=ingredient,
        indication=indication,
        applicant=applicant,
        manufacturer=manufacturer,
        form=form,
        drug_class=drug_class,
        country=country,
        limit=limit,
    )


@mcp.tool
async def search_by_ingredient(
    ingredient: str,
    limit_per_group: int = 10,
) -> SearchByIngredientResponse:
    """List all Taiwan FDA licenses for an active ingredient, grouped 單方 vs 複方.

    Use this (instead of `search_drugs`) when the user asks "which products
    contain ingredient X", "show me all the amlodipine products", or wants to
    compare single-ingredient vs fixed-dose-combination products for one active
    ingredient. Then pick a `license_no` from a group and call `get_package_insert`.

    Licenses are matched by case-insensitive substring on 主成分略述, then grouped
    by verbatim ingredient signature. Grouping is faithful to how TFDA registers
    each license: components are split on ';;' only, and salt forms are preserved
    exactly — 'AMLODIPINE BESYLATE' and 'AMLODIPINE BESILATE' are DISTINCT groups.
    The wrapper never decides salt-form equivalence.

    Args:
        ingredient: active-ingredient substring (e.g. "amlodipine", "valsartan").
        limit_per_group: max licenses listed within each group (default 10). The
            group's true size is always reported in `count`.

    Returns:
        SearchByIngredientResponse — `total_matched`, `mono_count`, `combo_count`,
        `group_count`, and `groups` (each with `components`, `is_mono`, `count`,
        and a truncated `licenses` list). Groups are sorted 單方-first, then by
        descending license count. `error` is set only when `ingredient` is blank.
    """
    return await _search_by_ingredient(
        ingredient=ingredient,
        limit_per_group=limit_per_group,
    )


@mcp.tool
async def get_package_insert(
    license_no: str,
    response_format: ResponseFormatLiteral = "key",
    fields: FieldGroupLiteral | list[str] | None = None,
) -> GetPackageInsertResponse:
    """Fetch the official package insert (仿單) for a Taiwan FDA drug license.

    Args:
        license_no: full Chinese license string (e.g. "衛署藥輸字第021571號").
        response_format: one of "concise" / "key" (default) / "detailed" / "full". Controls
            which fields are returned by default; overridden if `fields` is set explicitly.
            "concise" = name_zh + indication + special_warning + last_update_date; "key" =
            the safety-critical default set; "detailed" = all mapped sub-section fields;
            "full" = detailed + main_factories / sub_factories / companies lists + image
            data_url payloads. See ADR-0006 for the rationale.
        fields: explicit list of field names (overrides response_format). Either "key_fields",
            "all", or a list drawn from this exact set:
            Basic — name_zh, name_en, license_no, form, applicant, manufacturer,
            drug_class, valid_until;
            Pre-section (always returned; "" + listed in confirmed_absent when XML element empty) —
            special_warning (top-level <WARNING> element = 加框警語 / black box warning),
            characteristics (top-level <CHARACT> element = 特殊性狀);
            Indication — indication;
            Dosage — dosage (parent §3), dosage_general (§3.1), dosage_preparation (§3.2),
            dosage_special_populations (§3.3);
            Restrictions — contraindications (§4);
            Warnings — warnings (parent §5; no longer merges <WARNING>), abuse_dependence (§5.2),
            machine_operation (§5.3), lab_tests (§5.4), other_precautions (§5.5);
            Special populations — special_populations (parent §6), pregnancy (§6.1),
            lactation (§6.2), reproductive (§6.3), pediatric (§6.4), geriatric (§6.5),
            hepatic_impairment (§6.6), renal_impairment (§6.7), other_populations (§6.8);
            Interactions — interactions (§7);
            Adverse — side_effects (parent §8), adverse_clinical (§8.1), adverse_trial (§8.2),
            adverse_postmarketing (§8.3);
            Overdose — overdose (§9);
            Pharmacology — pharmacology (parent §10), mechanism_of_action (§10.1),
            pharmacodynamics (§10.2), nonclinical_safety (§10.3);
            Pharmacokinetics — pharmacokinetics (§11);
            Trials — clinical_trials (§12);
            Composition — ingredients (§1.1), excipients (§1.2), form_detail (§1.3), appearance (§1.4);
            Storage — packaging (§13.1), shelf_life (§13.2), storage_conditions (§13.3),
            storage_cautions (§13.4);
            Patient — patient_instructions (§14), other_info (§15);
            Metadata — insert_version (last_update_date is always returned as a top-level
            response field, not a `fields` entry).
            OTC drugs (非處方藥: 成藥/乙類成藥/甲類成藥/指示藥) are detected automatically by
            藥品類別 (<DTYPE>) and use a SEPARATE field set — usage (§2 用途, ≠ Rx indication),
            usage_precautions (§3 使用上注意事項), directions (§4 用法用量, ≠ Rx dosage),
            otc_warnings (§5 警語, ≠ Rx warnings), plus shared ingredients (§1.1) /
            excipients (§1.2) / packaging (§6) / characteristics. §3 and §5 are exposed only via
            those parent fields — their sub-section numbering varies per drug, so each sub-item's
            text (with its heading) is folded into the parent rather than given a brittle per-number
            field. The response `format` field is "otc". Rx-only fields are not valid for OTC and
            vice versa. Any §7+ tail (儲存方式/類別/適用時機/急救及解毒方法/…) is returned in
            `additional_sections`.
            Optional/empty fields return empty strings (TFDA preserves order even when empty).
            Unknown names returned in `unknown_fields` with `did_you_mean` for self-correction.

    Returns:
        Dict with license_no, format ("rx"/"otc"), fields (text per field), field_sections,
        confirmed_absent, additional_sections (text-bearing sections without a named field),
        images, source_url, human_url, retrieved_at, last_update_date.
        On unsupported license prefix or fetch failure, returns {"license_no": ..., "error": {...}}.
    """
    return await _get_package_insert(
        license_no=license_no, fields=fields, response_format=response_format
    )


@mcp.tool
async def check_insert_updates(
    since_date: str,
    license_list: list[str] | None = None,
    limit: int = 200,
) -> CheckInsertUpdatesResponse:
    """List Taiwan FDA drug inserts that were updated on or after the given date.

    Args:
        since_date: 'YYYY-MM-DD' — lower bound (inclusive).
        license_list: optional. If provided, only inserts whose license_no is in this list are returned.
        limit: max entries in `updates` (default 200, newest-first). `total`/`by_date` still
            reflect every update; `truncated` is true when the list was capped. A wide date
            range can match thousands of inserts — keep this bounded or narrow `since_date`.
            Pass 0 or a negative value to disable the cap.

    Returns:
        Dict with `total` (unique inserts updated — full count), `returned` (entries in
        `updates`), `truncated` (true iff capped at `limit`), `by_date` (histogram newest-first
        over ALL updates), `updates` (sorted by last_update_date desc, capped at `limit`),
        `batch_errors` (per-window failures from the underlying API — surfaced not swallowed),
        and `error: null`. The GetDrugDoc API caps each request at a 10-day window — this tool
        batches automatically; a single FDA outage in one batch does not lose the rest.
    """
    return await _check_insert_updates(
        since_date=since_date, license_list=license_list, limit=limit
    )


@mcp.resource("structure://rx-insert", mime_type="text/markdown")
def rx_insert_structure() -> str:
    """Reference: TFDA Rx (處方藥) insert structure — sections 1-15 + sub-sections."""
    return RX_INSERT_STRUCTURE_MD


@mcp.resource("structure://otc-insert", mime_type="text/markdown")
def otc_insert_structure() -> str:
    """Reference: TFDA OTC (非處方藥) insert structure — 6 sections + field-name map."""
    return OTC_INSERT_STRUCTURE_MD


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> PlainTextResponse:
    """Cheap liveness/readiness probe for the reverse proxy / orchestrator."""
    return PlainTextResponse("OK")


def main() -> None:
    """Console-script entry point.

    Runs over stdio by default (individual `uvx` use, unchanged). Set
    MCP_TRANSPORT=http to serve the shared institutional HTTP service
    (ADR-0010 Model B); TLS terminates at a reverse-proxy edge, not here.
    """
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    if settings.MCP_TRANSPORT == "http":
        mcp.run(
            transport="http",
            host=settings.MCP_HTTP_HOST,
            port=settings.MCP_HTTP_PORT,
            path=settings.MCP_HTTP_PATH,
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
