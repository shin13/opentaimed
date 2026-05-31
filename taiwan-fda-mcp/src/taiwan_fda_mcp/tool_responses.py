# path: src/taiwan_fda_mcp/tool_responses.py
# brief: Pydantic response models — the public wire contract for the 3 MCP tools.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorInfo(BaseModel):
    """Structured error block. `code` is the RCode name (string form, stable identifier)."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Stable error code identifier (e.g. 'INSERT_NOT_FOUND').")
    message: str = Field(
        description="Human-readable error message in Traditional Chinese or English."
    )


class Attribution(BaseModel):
    """Per-response attribution — distinguishes official data from third-party wrapper."""

    model_config = ConfigDict(frozen=True)

    data_source: str = Field(description="Authoritative data origin.")
    data_official: bool = Field(description="True iff `data_source` is a government/official body.")
    wrapper: str = Field(
        description="Identity of the (third-party) MCP server returning this response."
    )


class DrugLicenseRow(BaseModel):
    """Public-facing license row in search results.

    Narrower than the internal `models.DrugLicense` — only fields suitable
    for downstream consumption are exposed.
    """

    license_no: str = Field(description="許可證字號 (e.g. 衛署藥輸字第021571號).")
    name_zh: str = Field(description="中文品名.")
    name_en: str = Field(description="英文品名.")
    ingredient: str = Field(description="主成分略述.")
    form: str = Field(description="劑型.")
    manufacturers: list[str] = Field(
        default_factory=list,
        description="All registered 製造商 for this license (collapsed from duplicate rows).",
    )
    applicant: str = Field(description="申請商.")
    drug_class: str | None = Field(default=None, description="藥品類別 (may be null).")
    country: str = Field(default="", description="製造廠國別 (manufacturing-site country).")
    status: str = Field(default="有效", description="Dataset 37 = 未註銷, so always 有效 in MVP.")


class SearchDrugsResponse(BaseModel):
    """Response shape for `search_drugs`."""

    query: str = Field(description="Echo of the fuzzy query the caller passed (may be empty).")
    total_matched: int = Field(
        description="Total distinct licenses matching BEFORE limit truncation."
    )
    returned: int = Field(description="Number of rows actually in `results`.")
    truncated: bool = Field(
        description="True iff total_matched > returned (i.e. caller needs to refine or paginate)."
    )
    results: list[DrugLicenseRow] = Field(
        default_factory=list,
        description="Sorted by license-prefix authority (import/原廠 first), then name_zh.",
    )
    dataset_retrieved_at: str | None = Field(
        default=None,
        description="ISO 8601 UTC time the search index (Dataset 37) was last loaded.",
    )
    dataset_age_hours: float | None = Field(
        default=None,
        description="Age of the search index in hours at response time.",
    )
    is_stale: bool = Field(
        default=False,
        description=(
            "True if the search index is older than its TTL (a background refresh "
            "is in flight or last failed); results are still served from cache."
        ),
    )
    error: ErrorInfo | None = Field(default=None, description="Null on success.")


class UnknownFieldInfo(BaseModel):
    """One unknown field-name entry — annotated with closest valid match."""

    model_config = ConfigDict(frozen=True)

    input: str = Field(description="The unrecognised field name the caller passed.")
    did_you_mean: str | None = Field(
        description="Closest valid field name (via difflib), or null if none close enough."
    )


class AdditionalSection(BaseModel):
    """One insert section that carries text but has no named field in the active format.

    Supersedes the older `unmapped_sections` safety net: it returns the section's
    true `section_no` AND its verbatim `text`, so a section that this wrapper has
    not given a field name (e.g. an OTC §7+ block, or a future TFDA addition) is
    surfaced with its content intact rather than being silently dropped. Because
    the raw text and true section number are returned, there is no risk of the LLM
    fabricating a field mapping — quote `text` and cite `section_no` directly.
    """

    model_config = ConfigDict(frozen=True)

    section_no: str = Field(description="Section number as it appears in the FDA XML (e.g. '7', '16').")
    title: str = Field(description="Section title from the FDA XML (Traditional Chinese).")
    text: str = Field(description="Verbatim plain-text content of this section.")


class SectionTocEntry(BaseModel):
    """One entry in the `available_sections` table-of-contents.

    Lists EVERY populated section in the insert XML — including ones the caller
    did not request — so the LLM can see what else is available without fetching
    again. `field_name` is the wrapper field that returns this section's content
    (e.g. 'excipients' for §1.2), or None for tail sections this wrapper has not
    named (whose text lives in `additional_sections`).
    """

    model_config = ConfigDict(frozen=True)

    section_no: str = Field(description="Section number as in the FDA XML (e.g. '1.2', '6.5').")
    title: str = Field(description="Section title from the FDA XML (Traditional Chinese).")
    char_count: int = Field(description="Length in characters of this section's plain-text content.")
    field_name: str | None = Field(
        description=(
            "Wrapper field name that returns this section's content "
            "(e.g. 'excipients' for §1.2). None if not mapped to a named field."
        ),
    )


class ImageRef(BaseModel):
    """Metadata for one inline insert image (e.g. 藥品外觀).

    `data_url` carries the base64 payload as a `data:` URI ONLY when the caller
    requested `response_format="full"`; otherwise it is null so the LLM still
    knows an image exists (and which section) without paying the token cost.
    """

    model_config = ConfigDict(frozen=True)

    section_no: str = Field(description="Section number the image belongs to (e.g. '1.4').")
    caption: str = Field(description="Section title acting as the image caption.")
    mime: str = Field(description="MIME type (e.g. 'image/jpeg').")
    size_bytes: int = Field(description="Decoded payload size in bytes.")
    data_url: str | None = Field(
        default=None,
        description="`data:{mime};base64,...` URI — populated only when response_format='full'.",
    )


class FactoryEntity(BaseModel):
    """One <MAINFACTORY> / <SUBFACTORY> entry from the insert XML."""

    model_config = ConfigDict(frozen=True)

    number: str = Field(description="FACNO from XML (廠商編號).")
    name: str = Field(description="廠商名稱 (Chinese or English).")
    address: str = Field(description="完整地址.")


class CompanyEntity(BaseModel):
    """One <COMPANY> entry from the insert XML."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="國內藥商名稱.")
    address: str = Field(description="完整地址.")


class InsertVersionInfo(BaseModel):
    """One historical version of an insert (used in alternate_versions)."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(description="Version label from the FDA XML.")
    update_date: str | None = Field(
        description="ISO date when this version was published, or null."
    )


class GetPackageInsertResponse(BaseModel):
    """Response shape for `get_package_insert`.

    On success: `error` is null and all payload fields are populated.
    On failure: `error` is populated and payload fields are absent / default.

    Citation requirement (per spec §14): every clinical claim returned in
    `fields` is traceable via (source_url, retrieved_at, last_update_date,
    field_sections[field_name]) — all four are present on success.
    """

    model_config = ConfigDict(extra="forbid")

    license_no: str = Field(description="Echo of the license_no the caller passed.")
    error: ErrorInfo | None = Field(default=None, description="Null on success.")
    format: Literal["rx", "otc"] = Field(
        default="rx",
        description=(
            "Insert format dispatched from <DTYPE>: 'rx' (prescription) or 'otc' "
            "(成藥/指示藥). Field names differ between formats — see fields docs."
        ),
    )
    fields: dict[str, str] = Field(
        default_factory=dict,
        description="Map of field-name → plain-text content. Keys are stable identifiers (see RX_FIELDS).",
    )
    field_sections: dict[str, str] = Field(
        default_factory=dict,
        description="Map of clinical field-name → insert section number (e.g. 'contraindications': '4').",
    )
    source_url: str | None = Field(
        default=None,
        description="Machine URL — TFDA GetDrugDoc API XML for this license (200 OK; cite as evidence).",
    )
    human_url: str | None = Field(
        default=None,
        description="Human URL — official TFDA web page for this insert.",
    )
    retrieved_at: str | None = Field(
        default=None, description="ISO 8601 UTC timestamp of this fetch."
    )
    last_update_date: str | None = Field(
        default=None, description="ISO date of the insert's last TFDA update."
    )
    insert_version: str | None = Field(
        default=None, description="Version label from the FDA XML, or null."
    )
    alternate_versions: list[InsertVersionInfo] = Field(
        default_factory=list,
        description="Older insert versions returned alongside the newest (rare; usually empty).",
    )
    attribution: Attribution | None = Field(
        default=None,
        description="Origin metadata — official data vs third-party wrapper.",
    )
    unknown_fields: list[UnknownFieldInfo] | None = Field(
        default=None,
        description="Present iff the caller passed field names not in RX_FIELDS; each entry has did_you_mean.",
    )
    confirmed_absent: list[str] = Field(
        default_factory=list,
        description=(
            "Field names whose source XML element exists but is empty — TFDA "
            "structurally confirms this drug has no such information. Distinguishes "
            "'查無 BBW' (tool failure) from 'TFDA 確認此藥無 BBW' (positive clinical fact). "
            "Currently populated for: special_warning, characteristics."
        ),
    )
    additional_sections: list[AdditionalSection] = Field(
        default_factory=list,
        description=(
            "Sections that carry text but have no named field in the active format "
            "(e.g. OTC §7+, or a future TFDA addition). Each carries section_no + "
            "title + verbatim text — quote and cite directly. Replaces the older "
            "unmapped_sections safety net (which omitted the text)."
        ),
    )
    available_sections: list[SectionTocEntry] = Field(
        default_factory=list,
        description=(
            "Table of contents — every populated section in the insert XML, with "
            "section number, title, char count, and the wrapper field name (or null "
            "if unmapped). Always returned regardless of which fields the caller "
            "requested. Lets LLM clients see what else is in this drug's insert "
            "without a second tool call; never assume `fields` is exhaustive."
        ),
    )
    images: list[ImageRef] = Field(
        default_factory=list,
        description=(
            "Inline insert images (e.g. 藥品外觀). Metadata always present; data_url "
            "(base64) only populated when response_format='full'."
        ),
    )
    main_factories: list[FactoryEntity] = Field(
        default_factory=list,
        description="主製造廠 — from <MAINFACTORY> XML repeats. Empty list if absent.",
    )
    sub_factories: list[FactoryEntity] = Field(
        default_factory=list,
        description="分裝/包裝廠 — from <SUBFACTORY> XML repeats. Empty list if absent.",
    )
    companies: list[CompanyEntity] = Field(
        default_factory=list,
        description="國內藥商 — from <COMPANY> XML repeats. Empty list if absent.",
    )


class UpdateEntry(BaseModel):
    """One updated insert in `check_insert_updates.updates`."""

    model_config = ConfigDict(frozen=True)

    license_no: str
    name_zh: str
    last_update_date: str


class BatchError(BaseModel):
    """One per-window failure in `check_insert_updates.batch_errors`.

    Surfaced (not swallowed) so the caller knows part of the date range
    failed and can decide whether to retry that window.
    """

    model_config = ConfigDict(frozen=True)

    window: list[str] = Field(description="[start_iso, end_iso] of the 10-day batch that failed.")
    code: str = Field(description="RCode name.")
    message: str


class CheckInsertUpdatesResponse(BaseModel):
    """Response shape for `check_insert_updates`."""

    since_date: str = Field(description="Lower bound of the date range (echo, ISO format).")
    today: str | None = Field(default=None, description="Upper bound (defaults to today UTC).")
    error: ErrorInfo | None = Field(
        default=None, description="Top-level error if the whole call failed."
    )
    total: int = Field(default=0, description="Number of unique inserts updated in the window.")
    by_date: dict[str, int] = Field(
        default_factory=dict,
        description="Histogram of {YYYY-MM-DD: count}, sorted newest-first.",
    )
    updates: list[UpdateEntry] = Field(
        default_factory=list,
        description="List of updated inserts, sorted by last_update_date descending.",
    )
    batch_errors: list[BatchError] = Field(
        default_factory=list,
        description="Per-window failures (surfaced rather than swallowed).",
    )


__all__ = [
    "AdditionalSection",
    "Attribution",
    "BatchError",
    "CheckInsertUpdatesResponse",
    "CompanyEntity",
    "DrugLicenseRow",
    "ErrorInfo",
    "FactoryEntity",
    "GetPackageInsertResponse",
    "ImageRef",
    "InsertVersionInfo",
    "SearchDrugsResponse",
    "SectionTocEntry",
    "UnknownFieldInfo",
    "UpdateEntry",
]
