# path: src/taiwan_fda_mcp/sources/insert/parser.py
# brief: Parse GetDrugDoc XML responses into DrugInsert models.

import base64
import binascii
import html
from xml.etree import ElementTree as ET

from taiwan_fda_mcp.exceptions import InsertParseError, RCode
from taiwan_fda_mcp.models import DrugInsert, InsertImage, InsertSection


def parse_get_drug_doc(xml_bytes: bytes) -> list[DrugInsert]:
    """Parse a GetDrugDoc XML response.

    Returns:
        List of DrugInsert (may be empty if ROOTDOCUMENT is empty).

    Raises:
        InsertParseError: malformed XML or API <Error> response.
    """
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314
    except ET.ParseError as exc:
        raise InsertParseError(
            RCode.INSERT_PARSE_FAILED,
            f"Malformed XML: {exc}",
        ) from exc

    if root.tag == "Error":
        message_el = root.find("Message")
        msg = (
            (message_el.text or "FDA API error").strip()
            if message_el is not None
            else "FDA API error"
        )
        raise InsertParseError(
            RCode.INSERT_PARSE_FAILED,
            msg,
        )

    if root.tag != "ROOTDOCUMENT":
        raise InsertParseError(
            RCode.INSERT_PARSE_FAILED,
            f"Unexpected root element: {root.tag!r}",
        )

    inserts: list[DrugInsert] = []
    for doc in root.findall("DOCUMENT"):
        inserts.append(_parse_document(doc))
    return inserts


def _parse_document(doc: ET.Element) -> DrugInsert:
    info = doc.find("INFO")
    if info is None:
        raise InsertParseError(
            RCode.INSERT_PARSE_FAILED,
            "DOCUMENT missing INFO element",
        )

    def info_text(tag: str) -> str:
        el = info.find(tag)
        return (el.text or "").strip() if el is not None and el.text is not None else ""

    return DrugInsert(
        license_no=info_text("SNO"),
        name_zh=info_text("CNAME"),
        name_en=info_text("ENAME"),
        drug_type=info_text("DTYPE"),
        version=info_text("VERSION"),
        update_date=info_text("VDATE"),
        characteristics_html=_decoded_text(doc.find("CHARACT")),
        warning_html=_decoded_text(doc.find("WARNING")),
        sections=_parse_sections(doc.find("CONTENT")),
        main_factory=_parse_entities(
            doc.findall("MAINFACTORY"), name_tag="FACNAME", addr_tag="FACADD", no_tag="FACNO"
        ),
        sub_factories=_parse_entities(
            doc.findall("SUBFACTORY"), name_tag="FACNAME", addr_tag="FACADD", no_tag="FACNO"
        ),
        companies=_parse_entities(
            doc.findall("COMPANY"), name_tag="COMNAME", addr_tag="COMADD", no_tag="COMNO"
        ),
    )


def _decoded_text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return html.unescape(el.text).strip()


def _parse_sections(content_el: ET.Element | None) -> list[InsertSection]:
    if content_el is None:
        return []
    return [_parse_section(s) for s in content_el.findall("SECTION")]


def _parse_section(section_el: ET.Element) -> InsertSection:
    no = (section_el.findtext("NO") or "").strip()
    title = (section_el.findtext("TITLE") or "").strip()
    try:
        level = int(section_el.get("LEVEL", "1"))
    except ValueError:
        level = 1

    text_parts: list[str] = []
    images: list[InsertImage] = []
    for value_el in section_el.findall("VALUE"):
        vtype = value_el.get("type")
        if vtype == "text":
            decoded = _decoded_text(value_el)
            if decoded:
                text_parts.append(decoded)
        elif vtype == "image":
            images.append(_parse_image(value_el))
    text = "\n\n".join(text_parts)

    children = [_parse_section(child) for child in section_el.findall("SECTION")]
    return InsertSection(
        number=no, level=level, title=title, text=text, images=images, children=children
    )


def _parse_image(value_el: ET.Element) -> InsertImage:
    """Build an InsertImage from a `<VALUE type="image" encode="1">` element."""
    data = (value_el.text or "").strip()
    try:
        size_bytes = len(base64.b64decode(data, validate=True)) if data else 0
    except (binascii.Error, ValueError):
        size_bytes = 0
    return InsertImage(
        mime=value_el.get("mimetype", ""),
        size_bytes=size_bytes,
        data=data,
    )


def _parse_entities(
    elements: list[ET.Element], *, name_tag: str, addr_tag: str, no_tag: str
) -> list[dict[str, str]]:
    return [
        {
            "number": (el.findtext(no_tag) or "").strip(),
            "name": (el.findtext(name_tag) or "").strip(),
            "address": (el.findtext(addr_tag) or "").strip(),
        }
        for el in elements
    ]
