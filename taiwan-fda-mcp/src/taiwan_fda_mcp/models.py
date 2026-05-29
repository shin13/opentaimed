# path: src/taiwan_fda_mcp/models.py
# brief: Shared Pydantic models for drug-license metadata, insert content, and citations.

from datetime import datetime

from pydantic import BaseModel, Field


class DrugLicense(BaseModel):
    """One row from data.fda.gov.tw Dataset 37 (未註銷藥品許可證資料集)."""

    license_no: str = Field(description="許可證字號, e.g. 衛署藥輸字第021571號")
    name_zh: str = Field(description="中文品名")
    name_en: str = Field(description="英文品名")
    indication: str = Field(default="", description="適應症")
    form: str = Field(default="", description="劑型")
    ingredient: str = Field(default="", description="主成分略述")
    applicant: str = Field(default="", description="申請商名稱")
    manufacturer: str = Field(default="", description="製造商名稱")
    drug_class: str | None = Field(default=None, description="藥品類別")
    cancel_status: str = Field(default="", description="註銷狀態 (空字串表有效)")
    valid_until: str = Field(default="", description="有效日期 YYYY/MM/DD")
    last_change_date: str = Field(default="", description="異動日期 YYYY/MM/DD")


class InsertImage(BaseModel):
    """One `<VALUE type="image" encode="1">` payload attached to a section.

    TFDA embeds base64-encoded images (e.g. 藥品外觀) inline in the insert XML.
    The base64 string is retained verbatim; `size_bytes` is the decoded length.
    """

    mime: str = Field(default="", description="MIME type from the `mimetype` attribute.")
    size_bytes: int = Field(default=0, description="Decoded payload size in bytes.")
    data: str = Field(default="", description="Base64-encoded image payload (encode=1).")


class InsertSection(BaseModel):
    """One section of a 仿單."""

    number: str = Field(description="Section number like '3' or '3.1'")
    level: int = Field(description="1 = top-level section, 2 = subsection")
    title: str = Field(description="Section title")
    text: str = Field(
        default="", description="HTML-decoded body text (may contain inline HTML tags)"
    )
    images: list[InsertImage] = Field(
        default_factory=list, description="Inline base64 images on this section (usually empty)."
    )
    children: list["InsertSection"] = Field(default_factory=list)


class DrugInsert(BaseModel):
    """Parsed GetDrugDoc XML — one DOCUMENT entry."""

    license_no: str
    name_zh: str
    name_en: str
    drug_type: str = Field(default="")
    version: str = Field(default="")
    update_date: str = Field(default="")
    characteristics_html: str = Field(default="")
    warning_html: str = Field(default="")
    sections: list[InsertSection] = Field(default_factory=list)
    main_factory: list[dict[str, str]] = Field(default_factory=list)
    sub_factories: list[dict[str, str]] = Field(default_factory=list)
    companies: list[dict[str, str]] = Field(default_factory=list)


class Citation(BaseModel):
    """Citation envelope attached to every response."""

    source_name: str = "衛生福利部食品藥物管理署"
    source_type: str = Field(description="'仿單' | '許可證資料'")
    license_no: str | None = None
    section: str | None = None
    source_url: str
    retrieved_at: datetime
    last_update_date: str | None = None
    source_text: str = Field(description="Verbatim passage from the source")


InsertSection.model_rebuild()
