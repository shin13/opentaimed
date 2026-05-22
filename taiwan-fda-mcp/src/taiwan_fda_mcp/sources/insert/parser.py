# path: src/taiwan_fda_mcp/sources/insert/parser.py
# brief: Parse GetDrugDoc XML responses into DrugInsert models.

import html
from xml.etree import ElementTree as ET

from taiwan_fda_mcp.exceptions import InsertParseError, RCode
from taiwan_fda_mcp.models import DrugInsert, InsertSection


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
            doc.findall("MAINFACTORY"), name_tag="FACNAME", addr_tag="FACADD"
        ),
        sub_factories=_parse_entities(
            doc.findall("SUBFACTORY"), name_tag="FACNAME", addr_tag="FACADD"
        ),
        companies=_parse_entities(doc.findall("COMPANY"), name_tag="COMNAME", addr_tag="COMADD"),
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

    text = ""
    value_el = section_el.find("VALUE")
    if value_el is not None and value_el.get("type") == "text":
        text = _decoded_text(value_el)

    children = [_parse_section(child) for child in section_el.findall("SECTION")]
    return InsertSection(number=no, level=level, title=title, text=text, children=children)


def _parse_entities(
    elements: list[ET.Element], *, name_tag: str, addr_tag: str
) -> list[dict[str, str]]:
    return [
        {
            "name": (el.findtext(name_tag) or "").strip(),
            "address": (el.findtext(addr_tag) or "").strip(),
        }
        for el in elements
    ]
