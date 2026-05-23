# path: src/taiwan_fda_mcp/tools.py
# brief: Pure-Python tool entry points — wrap Layer 1 into MCP-friendly responses.

import difflib
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from taiwan_fda_mcp.config import Settings, get_settings
from taiwan_fda_mcp.exceptions import (
    InsertFetchError,
    InsertParseError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
)
from taiwan_fda_mcp.models import DrugInsert, DrugLicense, InsertSection
from taiwan_fda_mcp.sources.insert.client import fetch_drug_insert
from taiwan_fda_mcp.sources.insert.html_text import html_to_text
from taiwan_fda_mcp.sources.license_code import license_str_to_code
from taiwan_fda_mcp.sources.opendata.client import fetch_dataset37
from taiwan_fda_mcp.sources.opendata.dataset37 import (
    cache_is_fresh,
    load_from_cache,
    write_to_cache,
)
from taiwan_fda_mcp.sources.opendata.search import SearchField
from taiwan_fda_mcp.sources.opendata.search import search_drugs as _search

_logger = logging.getLogger(__name__)

# Process-level memo for Dataset 37 — stdio MCP server is long-running;
# avoid re-parsing 26K rows on every tool call. Restart server to refresh.
_LICENSES_CACHE: list[DrugLicense] | None = None


# Independent project disclaimer — surfaced in every get_package_insert response
# so end users see official-source-vs-third-party-wrapper distinction.
_ATTRIBUTION: dict[str, Any] = {
    "data_source": "Taiwan FDA (TFDA) — mcp.fda.gov.tw GetDrugDoc API + data.fda.gov.tw opendata",
    "data_official": True,
    "wrapper": "taiwan-fda-mcp (independent open-source project, NOT a TFDA product)",
}


KEY_FIELDS: list[str] = [
    "indication",
    "dosage",
    "contraindications",
    "warnings",
    "side_effects",
    "last_update_date",
]

ALL_FIELDS: list[str] = [
    # INFO block fields
    "name_zh",
    "name_en",
    "license_no",
    "insert_version",
    "last_update_date",
    # Dataset 37 fields (with XML fallback when row missing from cache)
    "applicant",
    "manufacturer",
    "form",
    "drug_class",
    "valid_until",
    # CONTENT section-based fields
    "indication",
    "dosage",
    "contraindications",
    "warnings",
    "interactions",
    "side_effects",
    "ingredients",
    "form_detail",
    "appearance",
    "pharmacology",
    "pharmacokinetics",
    "packaging",
    "storage_conditions",
]


async def _load_or_refresh_licenses(settings: Settings) -> list[DrugLicense]:
    """Load Dataset 37 with a process-level memo.

    On first call: load from disk cache (if fresh) or download from FDA. Memoise.
    Subsequent calls: return memo. Restart server to pick up cache changes.
    """
    global _LICENSES_CACHE
    if _LICENSES_CACHE is not None:
        return _LICENSES_CACHE

    cache_dir = settings.DATASET37_CACHE_DIR
    if cache_is_fresh(cache_dir, ttl_hours=settings.DATASET37_TTL_HOURS):
        loaded = load_from_cache(cache_dir)
        if loaded:
            _LICENSES_CACHE = loaded
            return loaded

    rows = await fetch_dataset37(settings.FDA_OPENDATA_BASE_URL)
    write_to_cache(rows, cache_dir)
    _LICENSES_CACHE = rows
    return rows


async def search_drugs(
    query: str,
    *,
    search_by: SearchField = "any",
    limit: int = 10,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Search Dataset 37 for drugs matching `query`.

    Dataset 37 = 「未註銷藥品許可證資料集」 — all rows are active by upstream
    definition, so `status` is always "有效" (kept in output for forward compat).

    Returns a dict with:
        - query, search_by: echo of input (for caller verification)
        - total_matched: total rows matching the keyword (before limit truncation)
        - returned: number of rows in `results`
        - truncated: True iff total_matched > returned
        - results: sorted by license-prefix authority then name_zh
        - error: None on success, {code, message} on failure
    """
    s = settings or get_settings()
    licenses = await _load_or_refresh_licenses(s)
    total, matches = _search(licenses, keyword=query, search_by=search_by, limit=limit)
    results = [
        {
            "license_no": r.license_no,
            "name_zh": r.name_zh,
            "name_en": r.name_en,
            "ingredient": r.ingredient,
            "form": r.form,
            "manufacturer": r.manufacturer,
            "applicant": r.applicant,
            "drug_class": r.drug_class,
            "status": "有效",
        }
        for r in matches
    ]
    return {
        "query": query,
        "search_by": search_by,
        "total_matched": total,
        "returned": len(results),
        "truncated": total > len(results),
        "results": results,
        "error": None,
    }


async def get_package_insert(
    license_no: str,
    *,
    fields: list[str] | Literal["all", "key_fields"] = "key_fields",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Fetch one license's package insert and return the requested fields.

    Unified response contract — every response has top-level `error`
    (None on success, {code, message} on failure). On success the payload
    keys (`fields`, `field_sections`, `source_url`, `human_url`,
    `retrieved_at`, `last_update_date`, `attribution`) are present.

    When FDA returns multiple inserts for one license (rare — usually
    historical versions), the newest by update_date is selected and the
    rest are surfaced in `alternate_versions`.

    Unknown field names are returned in `unknown_fields`, each annotated
    with `did_you_mean` (closest match from the valid list) so the caller
    can self-correct without refetching.
    """
    s = settings or get_settings()

    try:
        code = license_str_to_code(license_no)
    except (LicensePrefixUnsupportedError, InvalidLicenseError) as exc:
        return _error_response(license_no, exc.code.name, exc.message)

    try:
        inserts = await fetch_drug_insert(
            base_url=s.FDA_INSERT_BASE_URL,
            license_code=code,
            rate_limit_interval=s.FDA_RATE_LIMIT_INTERVAL_SECONDS,
        )
    except (InsertFetchError, InsertParseError) as exc:
        return _error_response(license_no, exc.code.name, exc.message)

    if not inserts:
        return _error_response(license_no, "INSERT_NOT_FOUND", "FDA API returned no documents")

    # Pick the newest insert; surface older ones as alternate_versions for transparency.
    inserts_sorted = sorted(inserts, key=lambda i: i.update_date or "", reverse=True)
    insert = inserts_sorted[0]
    alternates = [
        {"version": alt.version, "update_date": alt.update_date or None}
        for alt in inserts_sorted[1:]
    ]
    license_row = await _find_license_row(license_no, s)

    field_list = _resolve_fields(fields)
    known_fields = set(ALL_FIELDS)
    field_values: dict[str, str] = {}
    field_sections: dict[str, str] = {}
    unknown_fields: list[dict[str, str | None]] = []
    for f in field_list:
        if f not in known_fields:
            close = difflib.get_close_matches(f, ALL_FIELDS, n=1, cutoff=0.6)
            unknown_fields.append({"input": f, "did_you_mean": close[0] if close else None})
            continue
        value = _extract_field(f, insert=insert, license_row=license_row)
        if value:
            field_values[f] = value
            section_no = _SECTION_NUMBERS.get(f)
            if section_no:
                field_sections[f] = section_no
            elif f == "warnings":
                # warnings merges top-level <WARNING> + section 5; section 5 is the canonical citation.
                field_sections[f] = "5"

    response: dict[str, Any] = {
        "license_no": license_no,
        "error": None,
        "fields": field_values,
        # section_path per clinical field — satisfies spec §14 citation requirement
        # (every claim must cite source_url + retrieved_at + last_update_date + section).
        "field_sections": field_sections,
        # API URL (XML) — all 4 keys must be present or FDA returns HTTP 500.
        "source_url": (
            f"{s.FDA_INSERT_BASE_URL.rstrip('/')}/Serv/Query.asmx/GetDrugDoc"
            f"?license={code}&s_code=&startdate=&enddate="
        ),
        # Human-readable URL — official FDA web page for this insert.
        "human_url": (
            f"{s.FDA_INSERT_BASE_URL.rstrip('/')}/im_detail_1/{quote(license_no, safe='')}"
        ),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "last_update_date": insert.update_date or None,
        "insert_version": insert.version or None,
        "alternate_versions": alternates,
        "attribution": _ATTRIBUTION,
    }
    if unknown_fields:
        response["unknown_fields"] = unknown_fields
    return response


def _error_response(license_no: str, code: str, message: str) -> dict[str, Any]:
    """Unified failure shape for get_package_insert."""
    return {
        "license_no": license_no,
        "error": {"code": code, "message": message},
    }


async def check_insert_updates(
    since_date: str,
    *,
    license_list: list[str] | None = None,
    today: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Find inserts updated between `since_date` and `today` (inclusive).

    The GetDrugDoc API caps each call at a 10-day window — this function batches.

    Returns a dict with:
        - since_date, today: echo of the resolved date range
        - total: number of unique inserts updated in the window
        - by_date: histogram {YYYY-MM-DD: count}, sorted newest-first by key
        - updates: list of {license_no, name_zh, last_update_date} sorted
          by last_update_date descending
        - batch_errors: list of {window, error} for any batches that failed
          (the function continues past failures; surfaces them rather than
          silently dropping data)
        - error: top-level error if the whole call failed (e.g. invalid date)
    """
    s = settings or get_settings()
    try:
        start = date.fromisoformat(since_date)
    except ValueError as exc:
        return {
            "since_date": since_date,
            "today": today,
            "error": {"code": "INVALID_DATE", "message": str(exc)},
            "total": 0,
            "by_date": {},
            "updates": [],
            "batch_errors": [],
        }
    end = date.fromisoformat(today) if today else datetime.now(UTC).date()

    if end < start:
        return {
            "since_date": start.isoformat(),
            "today": end.isoformat(),
            "error": None,
            "total": 0,
            "by_date": {},
            "updates": [],
            "batch_errors": [],
        }

    filter_set = set(license_list) if license_list else None
    seen: dict[str, DrugInsert] = {}
    batch_errors: list[dict[str, Any]] = []

    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=9), end)
        try:
            inserts = await fetch_drug_insert(
                base_url=s.FDA_INSERT_BASE_URL,
                startdate=window_start.strftime("%Y/%m/%d"),
                enddate=window_end.strftime("%Y/%m/%d"),
                rate_limit_interval=s.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            )
        except (InsertFetchError, InsertParseError) as exc:
            _logger.warning(
                "check_insert_updates.batch_failed",
                extra={
                    "window": (window_start.isoformat(), window_end.isoformat()),
                    "error": exc.message,
                },
            )
            batch_errors.append(
                {
                    "window": [window_start.isoformat(), window_end.isoformat()],
                    "code": exc.code.name,
                    "message": exc.message,
                }
            )
            window_start = window_end + timedelta(days=1)
            continue

        for ins in inserts:
            if filter_set is not None and ins.license_no not in filter_set:
                continue
            seen[ins.license_no] = ins
        window_start = window_end + timedelta(days=1)

    updates = sorted(
        (
            {
                "license_no": ins.license_no,
                "name_zh": ins.name_zh,
                "last_update_date": ins.update_date,
            }
            for ins in seen.values()
        ),
        key=lambda u: u["last_update_date"] or "",
        reverse=True,
    )
    by_date_counter = Counter(u["last_update_date"] for u in updates if u["last_update_date"])
    by_date = dict(sorted(by_date_counter.items(), key=lambda kv: kv[0], reverse=True))

    return {
        "since_date": start.isoformat(),
        "today": end.isoformat(),
        "error": None,
        "total": len(updates),
        "by_date": by_date,
        "updates": updates,
        "batch_errors": batch_errors,
    }


# --- internals ----------------------------------------------------------------


async def _find_license_row(license_no: str, settings: Settings) -> DrugLicense | None:
    licenses = await _load_or_refresh_licenses(settings)
    return next((r for r in licenses if r.license_no == license_no), None)


def _resolve_fields(fields: list[str] | Literal["all", "key_fields"]) -> list[str]:
    if fields == "key_fields":
        return list(KEY_FIELDS)
    if fields == "all":
        return list(ALL_FIELDS)
    return list(fields)


# Section number per CONTENT field — kept as a single local map for clarity.
_SECTION_NUMBERS: dict[str, str] = {
    "ingredients": "1.1",
    "form_detail": "1.3",
    "appearance": "1.4",
    "indication": "2",
    "dosage": "3",
    "contraindications": "4",
    "interactions": "7",
    "side_effects": "8",
    "pharmacology": "10",
    "pharmacokinetics": "11",
    "packaging": "13.1",
    "storage_conditions": "13.3",
}


def _extract_field(  # noqa: PLR0911, PLR0912
    field: str,
    *,
    insert: DrugInsert,
    license_row: DrugLicense | None,
) -> str:
    """Resolve one field name to text. Read top-down: each field has one home."""
    # INFO block — always present in GetDrugDoc response
    if field == "name_zh":
        return insert.name_zh
    if field == "name_en":
        return insert.name_en
    if field == "license_no":
        return insert.license_no
    if field == "insert_version":
        return insert.version
    if field == "last_update_date":
        return insert.update_date

    # Dataset 37 fields — prefer cache row, fall back to GetDrugDoc XML
    # (so newly-issued licenses not yet in cache still get manufacturer / applicant).
    if field == "manufacturer":
        if license_row and license_row.manufacturer:
            return license_row.manufacturer
        return insert.main_factory[0].get("name", "") if insert.main_factory else ""
    if field == "applicant":
        if license_row and license_row.applicant:
            return license_row.applicant
        return insert.companies[0].get("name", "") if insert.companies else ""
    if field == "drug_class":
        if license_row and license_row.drug_class:
            return license_row.drug_class
        return insert.drug_type or ""
    if field == "form":
        return license_row.form if license_row else ""
    if field == "valid_until":
        return license_row.valid_until if license_row else ""

    # Special: warnings combines top-level <WARNING> with CONTENT section "5"
    # (real Rx inserts put警語 in either or both places).
    if field == "warnings":
        top = html_to_text(insert.warning_html)
        section5 = html_to_text(_section_text(insert.sections, "5"))
        if top and section5:
            return f"{top}\n\n{section5}"
        return top or section5

    # CONTENT section-based fields — strip HTML to plain text. Saves ~75% tokens
    # and prevents the LLM from leaking raw <p style="..."> markup into responses.
    section_no = _SECTION_NUMBERS.get(field)
    if section_no:
        return html_to_text(_section_text(insert.sections, section_no))

    return ""


def _section_text(sections: list[InsertSection], wanted_number: str) -> str:
    """Find a section by number; return its text + descendants' text concatenated."""
    parts: list[str] = []
    for section in sections:
        _walk(section, wanted_number, parts)
    return "\n\n".join(p for p in parts if p)


def _walk(section: InsertSection, wanted: str, out: list[str]) -> None:
    if section.number == wanted:
        if section.text:
            out.append(section.text)
        for child in section.children:
            if child.text:
                out.append(child.text)
        return
    for child in section.children:
        _walk(child, wanted, out)


__all__ = [
    "ALL_FIELDS",
    "KEY_FIELDS",
    "check_insert_updates",
    "get_package_insert",
    "search_drugs",
]
