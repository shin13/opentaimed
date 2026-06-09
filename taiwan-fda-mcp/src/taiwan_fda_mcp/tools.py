# path: src/taiwan_fda_mcp/tools.py
# brief: Pure-Python tool entry points — wrap Layer 1 into MCP-friendly responses.

import asyncio
import difflib
import logging
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from taiwan_fda_mcp.config import Settings, get_settings
from taiwan_fda_mcp.exceptions import (
    DatasetFetchError,
    InsertFetchError,
    InsertParseError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
)
from taiwan_fda_mcp.models import DrugInsert, DrugLicense, InsertSection
from taiwan_fda_mcp.sources.insert.client import fetch_drug_insert
from taiwan_fda_mcp.sources.insert.html_text import html_to_text
from taiwan_fda_mcp.sources.insert.throttle import InsertEgressThrottle, get_insert_throttle
from taiwan_fda_mcp.sources.license_code import license_str_to_code
from taiwan_fda_mcp.sources.opendata.client import fetch_dataset37
from taiwan_fda_mcp.sources.opendata.dataset37 import (
    cache_mtime,
    load_from_cache,
    write_to_cache,
)
from taiwan_fda_mcp.sources.opendata.search import search_drugs as _search
from taiwan_fda_mcp.tool_responses import (
    AdditionalSection,
    Attribution,
    BatchError,
    CheckInsertUpdatesResponse,
    CompanyEntity,
    DrugLicenseRow,
    ErrorInfo,
    FactoryEntity,
    GetPackageInsertResponse,
    ImageRef,
    InsertVersionInfo,
    SearchDrugsResponse,
    SectionTocEntry,
    UnknownFieldInfo,
    UpdateEntry,
)

_logger = logging.getLogger(__name__)

# Process-level memo for Dataset 37 — the stdio MCP server is long-running, so
# we avoid re-parsing 26K rows on every tool call. TTL-aware stale-while-revalidate
# (ADR-0009): once warm, a stale memo is served immediately while a single
# background task refreshes it, so a tool call never blocks on the download.
_LICENSES_CACHE: list[DrugLicense] | None = None
_LICENSES_LOADED_AT: float | None = None
_REFRESH_TASK: "asyncio.Task[None] | None" = None


# Independent project disclaimer — surfaced in every get_package_insert response
# so end users see official-source-vs-third-party-wrapper distinction.
_ATTRIBUTION = Attribution(
    data_source="Taiwan FDA (TFDA) — mcp.fda.gov.tw GetDrugDoc API + data.fda.gov.tw opendata",
    data_official=True,
    wrapper="taiwan-fda-mcp (independent open-source project, NOT a TFDA product)",
)


# Pre-section fields sourced from top-level <WARNING> / <CHARACT> XML elements —
# always returned (even when empty) so the LLM gets a positive "TFDA confirms
# absent" signal via `confirmed_absent`, distinct from "tool failed to fetch".
# OTC has no <WARNING>/BBW slot, so only characteristics is always-present there.
_RX_ALWAYS_PRESENT_FIELDS: tuple[str, ...] = ("special_warning", "characteristics")
_OTC_ALWAYS_PRESENT_FIELDS: tuple[str, ...] = ("characteristics",)

# Metadata exposed as TOP-LEVEL response fields, never inside the `fields` map.
# Requesting one explicitly is accepted (served top-level), not flagged unknown —
# it is simply not duplicated into `fields` (S4.1). `last_update_date` is always
# returned top-level; `insert_version` remains a `fields` entry (its top-level twin
# is a separate, intentional surface).
_TOP_LEVEL_METADATA_FIELDS: frozenset[str] = frozenset({"last_update_date"})

RX_KEY_FIELDS: list[str] = [
    "indication",
    "dosage",
    "contraindications",
    "excipients",
    "warnings",
    "side_effects",
    "special_warning",
]

RX_FIELDS: list[str] = [
    # INFO block fields
    "name_zh",
    "name_en",
    "license_no",
    "insert_version",
    # Dataset 37 fields (with XML fallback when row missing from cache)
    "applicant",
    "manufacturer",
    "form",
    "drug_class",
    "valid_until",
    # Pre-section (always returned; populates confirmed_absent when empty)
    "special_warning",
    "characteristics",
    # CONTENT — Rx structure (parents + sub-sections)
    "indication",
    "dosage",
    "dosage_general",
    "dosage_preparation",
    "dosage_special_populations",
    "contraindications",
    "warnings",
    "abuse_dependence",
    "machine_operation",
    "lab_tests",
    "other_precautions",
    "interactions",
    "side_effects",
    "adverse_clinical",
    "adverse_trial",
    "adverse_postmarketing",
    "ingredients",
    "excipients",
    "form_detail",
    "appearance",
    "pharmacology",
    "mechanism_of_action",
    "pharmacodynamics",
    "nonclinical_safety",
    "pharmacokinetics",
    "special_populations",
    "pregnancy",
    "lactation",
    "reproductive",
    "pediatric",
    "geriatric",
    "hepatic_impairment",
    "renal_impairment",
    "other_populations",
    "overdose",
    "clinical_trials",
    "packaging",
    "shelf_life",
    "storage_conditions",
    "storage_cautions",
    "patient_instructions",
    "other_info",
]

# OTC (非處方藥) field space — disjoint section meanings vs Rx (ADR-0007 §2,
# Strategy B). Field names are distinct where semantics differ (usage ≠ Rx
# indication, directions ≠ Rx dosage, otc_warnings ≠ Rx warnings) and shared
# where identical (ingredients / excipients / packaging / characteristics).
OTC_KEY_FIELDS: list[str] = [
    "usage",
    "directions",
    "otc_warnings",
    "ingredients",
]

OTC_FIELDS: list[str] = [
    # INFO block fields
    "name_zh",
    "name_en",
    "license_no",
    "insert_version",
    # Dataset 37 fields (with XML fallback when row missing from cache)
    "applicant",
    "manufacturer",
    "form",
    "drug_class",
    "valid_until",
    # Pre-section (OTC: characteristics only — no <WARNING>/BBW in OTC structure)
    "characteristics",
    # CONTENT — OTC structure (official top-level sections only; see
    # _OTC_SECTION_NUMBERS for why §3.x / §5.x sub-fields are not named)
    "ingredients",
    "excipients",
    "usage",
    "usage_precautions",
    "directions",
    "otc_warnings",
    "packaging",
]

ResponseFormat = Literal["concise", "key", "detailed", "full"]

# Field set per response_format, per format. `fields=` (explicit) overrides this.
_RX_RESPONSE_FORMAT_FIELDS: dict[ResponseFormat, list[str]] = {
    "concise": ["name_zh", "indication", "special_warning"],
    "key": RX_KEY_FIELDS,
    "detailed": RX_FIELDS,
    "full": RX_FIELDS,  # entity lists + image data_url additionally surfaced (not via fields)
}
_OTC_RESPONSE_FORMAT_FIELDS: dict[ResponseFormat, list[str]] = {
    "concise": ["name_zh", "usage"],
    "key": OTC_KEY_FIELDS,
    "detailed": OTC_FIELDS,
    "full": OTC_FIELDS,
}

# OTC discriminator categories (ADR-0007 附錄二). Anything else is Rx.
_OTC_CATEGORIES: frozenset[str] = frozenset(
    {
        "成藥",
        "乙類成藥",
        "甲類成藥",
        "須經醫師指示使用",
        "牙醫師指示使用",
        "醫師藥師藥劑生指示藥品",
    }
)


async def _load_or_refresh_licenses(settings: Settings) -> list[DrugLicense]:
    """Return Dataset 37 rows, refreshing in the background when stale (SWR).

    Cold start blocks once (disk cache, else a single download). After that a
    stale memo is served immediately while a single background task refreshes
    it — queries never block on the network mid-agent-turn. Implements ADR-0009.
    """
    if _LICENSES_CACHE is None:
        return await _cold_start(settings)

    ttl_seconds = settings.DATASET37_TTL_HOURS * 3600
    if _LICENSES_LOADED_AT is None or (time.time() - _LICENSES_LOADED_AT) >= ttl_seconds:
        _trigger_background_refresh(settings)  # serve stale now; refresh in background
    return _LICENSES_CACHE


async def _refresh_into_memo(settings: Settings) -> None:
    """Fetch Dataset 37 and replace the memo + disk cache; keep stale on failure."""
    global _LICENSES_CACHE, _LICENSES_LOADED_AT
    try:
        rows = await fetch_dataset37(
            settings.FDA_OPENDATA_BASE_URL,
            rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
        )
    except DatasetFetchError:
        _logger.warning("dataset37.refresh.failed")  # keep stale memo; retry next call
        return
    write_to_cache(rows, settings.DATASET37_CACHE_DIR)
    _LICENSES_CACHE, _LICENSES_LOADED_AT = rows, time.time()
    _logger.info("dataset37.refresh.done", extra={"count": len(rows)})


def _trigger_background_refresh(settings: Settings) -> None:
    """Schedule a single background refresh; no-op if one is already in flight."""
    global _REFRESH_TASK
    if _REFRESH_TASK is not None and not _REFRESH_TASK.done():
        return  # single in-flight guard
    _REFRESH_TASK = asyncio.create_task(_refresh_into_memo(settings))


def _dataset_freshness(settings: Settings) -> tuple[str | None, float | None, bool]:
    """Derive (retrieved_at ISO, age_hours, is_stale) for the currently-served memo.

    Read after `_load_or_refresh_licenses` so `_LICENSES_LOADED_AT` reflects the
    age of the data actually returned (SWR serves stale before a refresh lands).
    """
    if _LICENSES_LOADED_AT is None:
        return None, None, False
    age_hours = (time.time() - _LICENSES_LOADED_AT) / 3600
    retrieved_at = datetime.fromtimestamp(_LICENSES_LOADED_AT, UTC).isoformat()
    is_stale = age_hours >= settings.DATASET37_TTL_HOURS
    return retrieved_at, age_hours, is_stale


async def _cold_start(settings: Settings) -> list[DrugLicense]:
    """First load this process: serve disk cache (refresh if stale) else download once."""
    global _LICENSES_CACHE, _LICENSES_LOADED_AT
    disk = load_from_cache(settings.DATASET37_CACHE_DIR)  # None if absent
    if disk is not None:
        _LICENSES_CACHE = disk
        _LICENSES_LOADED_AT = cache_mtime(settings.DATASET37_CACHE_DIR)  # real on-disk age
        ttl_seconds = settings.DATASET37_TTL_HOURS * 3600
        if _LICENSES_LOADED_AT is None or (time.time() - _LICENSES_LOADED_AT) >= ttl_seconds:
            _trigger_background_refresh(settings)  # stale disk → serve + refresh, no block
        return disk

    await _refresh_into_memo(settings)  # truly first run → block once
    if _LICENSES_CACHE is None:
        raise DatasetFetchError(RCode.DATASET_FETCH_FAILED, "Dataset 37 unavailable on first run")
    return _LICENSES_CACHE


async def search_drugs(
    query: str = "",
    *,
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
    settings: Settings | None = None,
) -> SearchDrugsResponse:
    """Search Dataset 37 by flat, optional, AND-combined filters.

    Dataset 37 = 「未註銷藥品許可證資料集」 — all rows are active by upstream
    definition. At least one of `query` or a filter must be non-empty.

    See `SearchDrugsResponse` for the full response shape.
    """
    s = settings or get_settings()
    # Strip before the empty-criteria check so whitespace-only input is treated
    # the same as no input (search.py also strips) — a consistent error, not a
    # silent empty success.
    if not any(
        v.strip()
        for v in (query, name_zh, name_en, ingredient, indication, applicant, manufacturer,
                  form, drug_class, country)
    ):
        return SearchDrugsResponse(
            query=query,
            total_matched=0,
            returned=0,
            truncated=False,
            results=[],
            error=ErrorInfo(
                code=RCode.SEARCH_NO_CRITERIA.name,
                message="Provide at least one of query or a filter parameter.",
            ),
        )

    licenses = await _load_or_refresh_licenses(s)
    retrieved_at, age_hours, is_stale = _dataset_freshness(s)
    total, groups = _search(
        licenses,
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
    results = [
        DrugLicenseRow(
            license_no=g.license.license_no,
            name_zh=g.license.name_zh,
            name_en=g.license.name_en,
            ingredient=g.license.ingredient,
            form=g.license.form,
            manufacturers=g.manufacturers,
            applicant=g.license.applicant,
            drug_class=g.license.drug_class,
            country=g.license.country,
        )
        for g in groups
    ]
    return SearchDrugsResponse(
        query=query,
        total_matched=total,
        returned=len(results),
        truncated=total > len(results),
        results=results,
        dataset_retrieved_at=retrieved_at,
        dataset_age_hours=age_hours,
        is_stale=is_stale,
        error=None,
    )


def _armed_insert_throttle(s: Settings) -> InsertEgressThrottle:
    """Return the shared insert egress throttle, mutating its ``min_interval``
    to the configured ``INSERT_THROTTLE_MIN_INTERVAL_SECONDS``.

    Side effect: writes to the process-wide singleton (idempotent; the write
    is atomic under single-threaded asyncio)."""
    throttle = get_insert_throttle()
    throttle.min_interval = s.INSERT_THROTTLE_MIN_INTERVAL_SECONDS
    return throttle


async def get_package_insert(
    license_no: str,
    *,
    fields: list[str] | Literal["all", "key_fields"] | None = None,
    response_format: ResponseFormat = "key",
    settings: Settings | None = None,
) -> GetPackageInsertResponse:
    """Fetch one license's package insert and return the requested fields.

    `response_format` selects a default field set (concise / key / detailed /
    full); an explicit `fields` list overrides it for fine-grained selection.
    Entity lists (main_factories / sub_factories / companies) and image
    `data_url` payloads are surfaced only when `response_format="full"`.

    See `GetPackageInsertResponse` for the full response shape, including
    citation traceability (`field_sections` mapping each clinical field to
    its insert section number), self-correcting `unknown_fields`, the
    `confirmed_absent` trust signal, and `additional_sections` carrying any
    text-bearing section without a named field.

    When FDA returns multiple inserts for one license (rare — usually
    historical versions), the newest by update_date is selected and the
    rest are surfaced in `alternate_versions`.
    """
    s = settings or get_settings()

    try:
        code = license_str_to_code(license_no)
    except (LicensePrefixUnsupportedError, InvalidLicenseError) as exc:
        return _error_response(license_no, exc.code, exc.message)

    try:
        inserts = await fetch_drug_insert(
            base_url=s.FDA_INSERT_BASE_URL,
            license_code=code,
            rate_limit_interval=s.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            throttle=_armed_insert_throttle(s),
        )
    except (InsertFetchError, InsertParseError) as exc:
        return _error_response(license_no, exc.code, exc.message)

    if not inserts:
        return _error_response(license_no, RCode.INSERT_NOT_FOUND, "FDA API returned no documents")

    # Pick the newest insert; surface older ones as alternate_versions for transparency.
    inserts_sorted = sorted(inserts, key=lambda i: i.update_date or "", reverse=True)
    insert = inserts_sorted[0]
    alternates = [
        InsertVersionInfo(version=alt.version, update_date=alt.update_date or None)
        for alt in inserts_sorted[1:]
    ]
    license_row = await _find_license_row(license_no, s)

    fmt = _classify_format(insert)
    is_full = response_format == "full"

    # Dispatch the field space by format (ADR-0007 Strategy B). OTC reuses
    # numeric <NO> with different meanings, and carries content in nested
    # <TITLE>s, so it needs a separate section map + title-folding extraction.
    is_otc = fmt == "otc"
    section_numbers = _OTC_SECTION_NUMBERS if is_otc else _RX_SECTION_NUMBERS
    all_fields = OTC_FIELDS if is_otc else RX_FIELDS
    always_present = _OTC_ALWAYS_PRESENT_FIELDS if is_otc else _RX_ALWAYS_PRESENT_FIELDS

    field_list = _resolve_fields(fields, response_format, is_otc=is_otc)
    known_fields = set(all_fields)
    field_values: dict[str, str] = {}
    field_sections: dict[str, str] = {}
    unknown_fields: list[UnknownFieldInfo] = []
    for f in field_list:
        if f in _TOP_LEVEL_METADATA_FIELDS:
            # Exposed top-level (e.g. last_update_date) — never duplicated into `fields`.
            continue
        if f not in known_fields:
            close = difflib.get_close_matches(f, all_fields, n=1, cutoff=0.6)
            unknown_fields.append(
                UnknownFieldInfo(input=f, did_you_mean=close[0] if close else None)
            )
            continue
        value = _extract_field(
            f, insert=insert, license_row=license_row, section_numbers=section_numbers, fold_titles=is_otc
        )
        # Always-present pre-section fields stay in `fields` even when empty
        # (positive "TFDA confirms absent" signal); others only when non-empty.
        if value or f in always_present:
            field_values[f] = value
            section_no = section_numbers.get(f)
            if section_no:
                field_sections[f] = section_no

    confirmed_absent = [
        f for f in always_present if f in field_values and not field_values[f]
    ]

    additional = _build_additional_sections(insert.sections, set(section_numbers.values()))
    available = _build_section_toc(
        insert.sections, {v: k for k, v in section_numbers.items()}, fold_titles=is_otc
    )
    images = _build_images(insert.sections, include_data=is_full)
    main_factories = _build_factories(insert.main_factory) if is_full else []
    sub_factories = _build_factories(insert.sub_factories) if is_full else []
    companies = _build_companies(insert.companies) if is_full else []

    return GetPackageInsertResponse(
        license_no=license_no,
        error=None,
        format=fmt,
        fields=field_values,
        field_sections=field_sections,
        source_url=(
            f"{s.FDA_INSERT_BASE_URL.rstrip('/')}/Serv/Query.asmx/GetDrugDoc"
            f"?license={code}&s_code=&startdate=&enddate="
        ),
        human_url=f"{s.FDA_INSERT_BASE_URL.rstrip('/')}/im_detail_1/{quote(license_no, safe='')}",
        retrieved_at=datetime.now(UTC).isoformat(),
        last_update_date=insert.update_date or None,
        insert_version=insert.version or None,
        alternate_versions=alternates,
        attribution=_ATTRIBUTION,
        unknown_fields=unknown_fields if unknown_fields else None,
        confirmed_absent=confirmed_absent,
        additional_sections=additional,
        available_sections=available,
        images=images,
        main_factories=main_factories,
        sub_factories=sub_factories,
        companies=companies,
    )


def _error_response(license_no: str, code: RCode, message: str) -> GetPackageInsertResponse:
    """Unified failure shape for get_package_insert."""
    return GetPackageInsertResponse(
        license_no=license_no,
        error=ErrorInfo(code=code.name, message=message),
    )


async def check_insert_updates(
    since_date: str,
    *,
    license_list: list[str] | None = None,
    today: str | None = None,
    limit: int = 200,
    settings: Settings | None = None,
) -> CheckInsertUpdatesResponse:
    """Find inserts updated between `since_date` and `today` (inclusive).

    The GetDrugDoc API caps each call at a 10-day window — this function
    batches automatically. Per-batch failures are SURFACED in `batch_errors`
    rather than silently dropping the failed window's data.

    `updates` is capped at `limit` (default 200, newest-first) to bound the
    response size; `total`, `returned`, `truncated`, and `by_date` let the
    caller tell whether anything was cut. A non-positive `limit` disables the
    cap. See `CheckInsertUpdatesResponse` for the full response shape.
    """
    s = settings or get_settings()
    try:
        start = date.fromisoformat(since_date)
    except ValueError as exc:
        return CheckInsertUpdatesResponse(
            since_date=since_date,
            today=today,
            error=ErrorInfo(code="INVALID_DATE", message=str(exc)),
        )
    end = date.fromisoformat(today) if today else datetime.now(UTC).date()

    if end < start:
        return CheckInsertUpdatesResponse(
            since_date=start.isoformat(),
            today=end.isoformat(),
            error=None,
        )

    filter_set = set(license_list) if license_list else None
    seen: dict[str, DrugInsert] = {}
    batch_errors: list[BatchError] = []

    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=9), end)
        try:
            inserts = await fetch_drug_insert(
                base_url=s.FDA_INSERT_BASE_URL,
                startdate=window_start.strftime("%Y/%m/%d"),
                enddate=window_end.strftime("%Y/%m/%d"),
                rate_limit_interval=s.FDA_RATE_LIMIT_INTERVAL_SECONDS,
                throttle=_armed_insert_throttle(s),
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
                BatchError(
                    window=[window_start.isoformat(), window_end.isoformat()],
                    code=exc.code.name,
                    message=exc.message,
                )
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
            UpdateEntry(
                license_no=ins.license_no,
                name_zh=ins.name_zh,
                last_update_date=ins.update_date,
            )
            for ins in seen.values()
        ),
        key=lambda u: u.last_update_date or "",
        reverse=True,
    )
    by_date_counter = Counter(u.last_update_date for u in updates if u.last_update_date)
    by_date = dict(sorted(by_date_counter.items(), key=lambda kv: kv[0], reverse=True))

    total = len(updates)
    returned_updates = updates[:limit] if limit > 0 else updates

    return CheckInsertUpdatesResponse(
        since_date=start.isoformat(),
        today=end.isoformat(),
        error=None,
        total=total,
        returned=len(returned_updates),
        truncated=total > len(returned_updates),
        by_date=by_date,
        updates=returned_updates,
        batch_errors=batch_errors,
    )


# --- internals ----------------------------------------------------------------


async def _find_license_row(license_no: str, settings: Settings) -> DrugLicense | None:
    licenses = await _load_or_refresh_licenses(settings)
    return next((r for r in licenses if r.license_no == license_no), None)


def _resolve_fields(
    fields: list[str] | Literal["all", "key_fields"] | None,
    response_format: ResponseFormat,
    *,
    is_otc: bool,
) -> list[str]:
    if fields is None:
        rf_map = _OTC_RESPONSE_FORMAT_FIELDS if is_otc else _RX_RESPONSE_FORMAT_FIELDS
        return list(rf_map[response_format])
    if fields == "key_fields":
        return list(OTC_KEY_FIELDS if is_otc else RX_KEY_FIELDS)
    if fields == "all":
        return list(OTC_FIELDS if is_otc else RX_FIELDS)
    return list(fields)


def _classify_format(insert: DrugInsert) -> Literal["rx", "otc"]:
    """Dispatch Rx vs OTC from <DTYPE> (ADR-0007 附錄二), with a structural check.

    `成藥`/指示藥 categories → OTC (separate field space + title-folding
    extraction); everything else → Rx. A structural cross-check (§1 title ==
    "成分") logs a warning on mismatch so a miscategorised insert is visible.
    """
    fmt: Literal["rx", "otc"] = "otc" if insert.drug_type in _OTC_CATEGORIES else "rx"
    first = insert.sections[0] if insert.sections else None
    structural_otc = bool(first and first.number == "1" and first.title == "成分")
    if structural_otc and fmt == "rx":
        _logger.warning(
            "insert.format.mismatch",
            extra={"drug_type": insert.drug_type, "section1_title": first.title if first else None},
        )
    return fmt


# Section number per Rx CONTENT field. Parents (e.g. "3") fold their sub-sections;
# sub-sections (e.g. "3.1") are individually addressable for precise citation.
_RX_SECTION_NUMBERS: dict[str, str] = {
    # Section 1 — 性狀
    "ingredients": "1.1",
    "excipients": "1.2",
    "form_detail": "1.3",
    "appearance": "1.4",
    # Section 2 — 適應症
    "indication": "2",
    # Section 3 — 用法及用量 (parent + sub-sections)
    "dosage": "3",
    "dosage_general": "3.1",
    "dosage_preparation": "3.2",
    "dosage_special_populations": "3.3",
    # Section 4 — 禁忌
    "contraindications": "4",
    # Section 5 — 警語及注意事項 (parent + sub-sections; no longer merges <WARNING>)
    "warnings": "5",
    "abuse_dependence": "5.2",
    "machine_operation": "5.3",
    "lab_tests": "5.4",
    "other_precautions": "5.5",
    # Section 6 — 特殊族群之用藥 (parent + sub-sections)
    "special_populations": "6",
    "pregnancy": "6.1",
    "lactation": "6.2",
    "reproductive": "6.3",
    "pediatric": "6.4",
    "geriatric": "6.5",
    "hepatic_impairment": "6.6",
    "renal_impairment": "6.7",
    "other_populations": "6.8",
    # Section 7 — 交互作用
    "interactions": "7",
    # Section 8 — 副作用/不良反應 (parent + sub-sections)
    "side_effects": "8",
    "adverse_clinical": "8.1",
    "adverse_trial": "8.2",
    "adverse_postmarketing": "8.3",
    # Section 9 — 過量
    "overdose": "9",
    # Section 10 — 藥理特性 (parent + sub-sections)
    "pharmacology": "10",
    "mechanism_of_action": "10.1",
    "pharmacodynamics": "10.2",
    "nonclinical_safety": "10.3",
    # Section 11 — 藥物動力學
    "pharmacokinetics": "11",
    # Section 12 — 臨床試驗資料
    "clinical_trials": "12",
    # Section 13 — 藥品保存
    "packaging": "13.1",
    "shelf_life": "13.2",
    "storage_conditions": "13.3",
    "storage_cautions": "13.4",
    # Section 14 — 病人使用須知
    "patient_instructions": "14",
    # Section 15 — 其他
    "other_info": "15",
}

# Section number per OTC CONTENT field (ADR-0007 §2 / 衛福部 105.03.08 公告).
# OTC reuses numeric <NO> with DIFFERENT meanings than Rx — hence a separate
# dict dispatched by <DTYPE>.
#
# Only the OFFICIAL top-level sections are named. Live verification (2026-05-30)
# showed OTC §3.x / §5.x sub-numbering is NOT stable across drugs — e.g. one
# insert's §3.2 is 諮詢藥師 while another's §3.2 is 洽醫師. A by-position sub-field
# map therefore mislabels content, so §3 / §5 are exposed only via their stable
# parents (`usage_precautions` / `otc_warnings`); title-folding keeps every
# sub-item's text (with its heading) inside the parent. §7+ tail (儲存方式/類別/
# 適用時機/急救及解毒方法/…) is NOT named — it flows through additional_sections.
_OTC_SECTION_NUMBERS: dict[str, str] = {
    # Section 1 — 成分
    "ingredients": "1.1",
    "excipients": "1.2",
    # Section 2 — 用途(適應症)  ← distinct from Rx `indication`
    "usage": "2",
    # Section 3 — 使用上注意事項 (parent only; sub-numbering varies per drug)
    "usage_precautions": "3",
    # Section 4 — 用法用量  ← distinct from Rx `dosage`
    "directions": "4",
    # Section 5 — 警語 (parent only; sub-numbering varies per drug)
    "otc_warnings": "5",
    # Section 6 — 包裝
    "packaging": "6",
}

# Invariant: within each format, every section number maps from at most one field
# (the TOC inverts these dicts, which would silently drop entries on collision).
for _fmt_name, _fmt_map in (("rx", _RX_SECTION_NUMBERS), ("otc", _OTC_SECTION_NUMBERS)):
    assert len(set(_fmt_map.values())) == len(_fmt_map), (
        f"duplicate section numbers in {_fmt_name} map"
    )


def _extract_field(  # noqa: PLR0911, PLR0912
    field: str,
    *,
    insert: DrugInsert,
    license_row: DrugLicense | None,
    section_numbers: dict[str, str],
    fold_titles: bool,
) -> str:
    """Resolve one field name to text. Read top-down: each field has one home.

    `section_numbers` is the active format's section map (Rx or OTC) and
    `fold_titles` enables OTC's nested-<TITLE> content folding.
    """
    # INFO block — always present in GetDrugDoc response
    if field == "name_zh":
        return insert.name_zh
    if field == "name_en":
        return insert.name_en
    if field == "license_no":
        return insert.license_no
    if field == "insert_version":
        return insert.version
    # NOTE: last_update_date is served as a top-level response field
    # (_TOP_LEVEL_METADATA_FIELDS), not via this resolver — no branch here.

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

    # Special: special_warning sources from the top-level <WARNING> XML element
    # — TFDA's 加框警語 / black box warning (BBW) pre-section, distinct from §5
    # 警語及注意事項. MUST-quote rule applies (see server instructions). When empty,
    # the response builder additionally records this field in `confirmed_absent`.
    if field == "special_warning":
        return html_to_text(insert.warning_html)
    # Special: characteristics sources from the top-level <CHARACT> XML element
    # — 特殊性狀 pre-section. Same confirmed_absent treatment.
    if field == "characteristics":
        return html_to_text(insert.characteristics_html)
    # warnings (§5) now maps purely to section 5 — no longer merges <WARNING>.

    # CONTENT section-based fields — strip HTML to plain text. Saves ~75% tokens
    # and prevents the LLM from leaking raw <p style="..."> markup into responses.
    section_no = section_numbers.get(field)
    if section_no:
        return _field_text(insert.sections, section_no, fold_titles=fold_titles)

    return ""


def _build_additional_sections(
    sections: list[InsertSection],
    mapped_numbers: set[str],
) -> list[AdditionalSection]:
    """Return text-bearing sections that have no named field (with verbatim text).

    Supersedes the old `unmapped_sections` net: it carries the section's true
    number, title, AND text, so an unmapped section (e.g. OTC §7+, or a future
    TFDA addition) is surfaced with content intact rather than silently dropped.
    Ancestor check: when a parent number (e.g. "10") is mapped, `_section_text`
    folds its descendants into the parent's field — listing those descendants
    here would be a false "missing" signal, so they are suppressed.
    """
    out: list[AdditionalSection] = []

    def has_mapped_ancestor(number: str) -> bool:
        """Is any dot-prefix of `number` in mapped_numbers? e.g. "10.1" → check "10"."""
        parts = number.split(".")
        return any(
            ".".join(parts[:i]) in mapped_numbers for i in range(len(parts) - 1, 0, -1)
        )

    def walk(section: InsertSection) -> None:
        for child in section.children:
            walk(child)
        if (
            section.text
            and section.number not in mapped_numbers
            and not has_mapped_ancestor(section.number)
        ):
            out.append(
                AdditionalSection(
                    section_no=section.number,
                    title=section.title,
                    text=html_to_text(section.text),
                )
            )

    for s in sections:
        walk(s)
    out.sort(key=lambda a: a.section_no)
    return out


def _build_images(sections: list[InsertSection], *, include_data: bool) -> list[ImageRef]:
    """Flatten inline section images into ImageRef metadata.

    `data_url` is populated only when `include_data` (response_format='full');
    otherwise it stays null so the LLM still knows an image exists.
    """
    out: list[ImageRef] = []

    def walk(section: InsertSection) -> None:
        for img in section.images:
            data_url = f"data:{img.mime};base64,{img.data}" if include_data and img.data else None
            out.append(
                ImageRef(
                    section_no=section.number,
                    caption=section.title,
                    mime=img.mime,
                    size_bytes=img.size_bytes,
                    data_url=data_url,
                )
            )
        for child in section.children:
            walk(child)

    for s in sections:
        walk(s)
    return out


def _build_factories(entities: list[dict[str, str]]) -> list[FactoryEntity]:
    return [
        FactoryEntity(
            number=e.get("number", ""),
            name=e.get("name", ""),
            address=e.get("address", ""),
        )
        for e in entities
    ]


def _build_companies(entities: list[dict[str, str]]) -> list[CompanyEntity]:
    return [
        CompanyEntity(name=e.get("name", ""), address=e.get("address", ""))
        for e in entities
    ]


def _build_section_toc(
    sections: list[InsertSection],
    section_to_field: dict[str, str],
    *,
    fold_titles: bool,
) -> list[SectionTocEntry]:
    """Build a flat TOC of every section that carries content or is a named field.

    A section is listed if it has its own <VALUE> text OR maps to a wrapper field
    (so OTC §3.x, whose content is title-borne, still appears). `char_count` is the
    length of the resolved (folded for OTC) text; `field_name` comes from the
    inverse of the active section map (None for unmapped/tail sections).
    """
    out: list[SectionTocEntry] = []

    def walk(section: InsertSection) -> None:
        for child in section.children:
            walk(child)
        field_name = section_to_field.get(section.number)
        if section.text or field_name is not None:
            text = _resolve_section_text(section, fold_titles=fold_titles)
            out.append(
                SectionTocEntry(
                    section_no=section.number,
                    title=section.title,
                    char_count=len(text),
                    field_name=field_name,
                )
            )

    for s in sections:
        walk(s)
    out.sort(key=lambda e: e.section_no)
    return out


def _find_section(sections: list[InsertSection], wanted: str) -> InsertSection | None:
    """Depth-first search for the first section whose number == `wanted`."""
    for section in sections:
        if section.number == wanted:
            return section
        found = _find_section(section.children, wanted)
        if found is not None:
            return found
    return None


def _resolve_section_text(section: InsertSection, *, fold_titles: bool) -> str:
    """Plain text of a section's whole subtree.

    Collects each node's <VALUE> text. When `fold_titles` (OTC), a node with no
    <VALUE> contributes its <TITLE> instead — OTC inserts carry content in nested
    <TITLE> elements (e.g. §3.1.1 「曾因本藥成分引起過敏的人。」). Nodes with a blank
    <NO> (the malformed duplicate placeholders in some OTC §5 警語 blocks) are
    skipped to avoid double-rendering.
    """
    parts: list[str] = []

    def collect(s: InsertSection) -> None:
        if s.number == "" and s is not section:
            return
        if s.text:
            parts.append(html_to_text(s.text))
        elif fold_titles and s.title:
            parts.append(s.title)
        for child in s.children:
            collect(child)

    collect(section)
    return "\n\n".join(p for p in parts if p)


def _field_text(sections: list[InsertSection], wanted_number: str, *, fold_titles: bool) -> str:
    """Resolve a named field's section number to its full subtree text."""
    node = _find_section(sections, wanted_number)
    return _resolve_section_text(node, fold_titles=fold_titles) if node else ""


__all__ = [
    "OTC_FIELDS",
    "OTC_KEY_FIELDS",
    "RX_FIELDS",
    "RX_KEY_FIELDS",
    "check_insert_updates",
    "get_package_insert",
    "search_drugs",
]
