# path: src/taiwan_fda_mcp/tools.py
# brief: Pure-Python tool entry points — wrap Layer 1 into MCP-friendly responses.

import difflib
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import quote

from taiwan_fda_mcp.config import Settings, get_settings
from taiwan_fda_mcp.exceptions import (
    InsertFetchError,
    InsertParseError,
    InvalidLicenseError,
    LicensePrefixUnsupportedError,
    RCode,
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
    UnknownFieldInfo,
    UpdateEntry,
)

_logger = logging.getLogger(__name__)

# Process-level memo for Dataset 37 — stdio MCP server is long-running;
# avoid re-parsing 26K rows on every tool call. Restart server to refresh.
_LICENSES_CACHE: list[DrugLicense] | None = None


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
_ALWAYS_PRESENT_FIELDS: tuple[str, ...] = ("special_warning", "characteristics")

RX_KEY_FIELDS: list[str] = [
    "indication",
    "dosage",
    "contraindications",
    "excipients",
    "warnings",
    "side_effects",
    "special_warning",
    "last_update_date",
]

RX_FIELDS: list[str] = [
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

ResponseFormat = Literal["concise", "key", "detailed", "full"]

# Field set per response_format. `fields=` (explicit) overrides this when set.
_RESPONSE_FORMAT_FIELDS: dict[ResponseFormat, list[str]] = {
    "concise": ["name_zh", "indication", "special_warning", "last_update_date"],
    "key": RX_KEY_FIELDS,
    "detailed": RX_FIELDS,
    "full": RX_FIELDS,  # entity lists + image data_url additionally surfaced (not via fields)
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
) -> SearchDrugsResponse:
    """Search Dataset 37 for drugs matching `query`.

    Dataset 37 = 「未註銷藥品許可證資料集」 — all rows are active by upstream
    definition, so `status` is always "有效" (kept in output for forward compat).

    See `SearchDrugsResponse` for the full response shape.
    """
    s = settings or get_settings()
    licenses = await _load_or_refresh_licenses(s)
    total, matches = _search(licenses, keyword=query, search_by=search_by, limit=limit)
    results = [
        DrugLicenseRow(
            license_no=r.license_no,
            name_zh=r.name_zh,
            name_en=r.name_en,
            ingredient=r.ingredient,
            form=r.form,
            manufacturer=r.manufacturer,
            applicant=r.applicant,
            drug_class=r.drug_class,
        )
        for r in matches
    ]
    return SearchDrugsResponse(
        query=query,
        search_by=search_by,
        total_matched=total,
        returned=len(results),
        truncated=total > len(results),
        results=results,
        error=None,
    )


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

    field_list = _resolve_fields(fields, response_format)
    known_fields = set(RX_FIELDS)
    field_values: dict[str, str] = {}
    field_sections: dict[str, str] = {}
    unknown_fields: list[UnknownFieldInfo] = []
    for f in field_list:
        if f not in known_fields:
            close = difflib.get_close_matches(f, RX_FIELDS, n=1, cutoff=0.6)
            unknown_fields.append(
                UnknownFieldInfo(input=f, did_you_mean=close[0] if close else None)
            )
            continue
        value = _extract_field(f, insert=insert, license_row=license_row)
        # Always-present pre-section fields stay in `fields` even when empty
        # (positive "TFDA confirms absent" signal); others only when non-empty.
        if value or f in _ALWAYS_PRESENT_FIELDS:
            field_values[f] = value
            section_no = _RX_SECTION_NUMBERS.get(f)
            if section_no:
                field_sections[f] = section_no

    confirmed_absent = [
        f for f in _ALWAYS_PRESENT_FIELDS if f in field_values and not field_values[f]
    ]

    additional = _build_additional_sections(insert.sections, set(_RX_SECTION_NUMBERS.values()))
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
    settings: Settings | None = None,
) -> CheckInsertUpdatesResponse:
    """Find inserts updated between `since_date` and `today` (inclusive).

    The GetDrugDoc API caps each call at a 10-day window — this function
    batches automatically. Per-batch failures are SURFACED in `batch_errors`
    rather than silently dropping the failed window's data.

    See `CheckInsertUpdatesResponse` for the full response shape.
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

    return CheckInsertUpdatesResponse(
        since_date=start.isoformat(),
        today=end.isoformat(),
        error=None,
        total=len(updates),
        by_date=by_date,
        updates=updates,
        batch_errors=batch_errors,
    )


# --- internals ----------------------------------------------------------------


async def _find_license_row(license_no: str, settings: Settings) -> DrugLicense | None:
    licenses = await _load_or_refresh_licenses(settings)
    return next((r for r in licenses if r.license_no == license_no), None)


def _resolve_fields(
    fields: list[str] | Literal["all", "key_fields"] | None,
    response_format: ResponseFormat,
) -> list[str]:
    if fields is None:
        return list(_RESPONSE_FORMAT_FIELDS[response_format])
    if fields == "key_fields":
        return list(RX_KEY_FIELDS)
    if fields == "all":
        return list(RX_FIELDS)
    return list(fields)


def _classify_format(insert: DrugInsert) -> Literal["rx", "otc"]:
    """Dispatch Rx vs OTC from <DTYPE> (ADR-0007 附錄二), with a structural check.

    Note: in this phase the OTC field space is not yet built — `format` is
    surfaced for transparency, but extraction always uses the Rx map. OTC
    dispatch lands in Phase 3.2.
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
    section_no = _RX_SECTION_NUMBERS.get(field)
    if section_no:
        return html_to_text(_section_text(insert.sections, section_no))

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
    "RX_FIELDS",
    "RX_KEY_FIELDS",
    "check_insert_updates",
    "get_package_insert",
    "search_drugs",
]
