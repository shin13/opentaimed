# path: tests/unit/test_insert_parser.py
# brief: Verify GetDrugDoc XML → DrugInsert parsing.

import base64
from pathlib import Path

import pytest

from taiwan_fda_mcp.exceptions import InsertParseError
from taiwan_fda_mcp.sources.insert.parser import parse_get_drug_doc


@pytest.fixture
def xml_bytes(fixtures_dir: Path) -> bytes:
    return (fixtures_dir / "getdrugdoc_sample.xml").read_bytes()


def test_parses_one_document(xml_bytes):
    inserts = parse_get_drug_doc(xml_bytes)
    assert len(inserts) == 1


def test_info_fields(xml_bytes):
    insert = parse_get_drug_doc(xml_bytes)[0]
    assert insert.license_no == "衛署藥輸字第021571號"
    assert insert.name_zh == "脈優錠５毫克"
    assert insert.name_en == "NORVASC TABLETS 5MG"
    assert insert.drug_type == "須由醫師處方使用"
    assert insert.version == "3"
    assert insert.update_date == "2025-10-29"


def test_warning_html_decoded(xml_bytes):
    insert = parse_get_drug_doc(xml_bytes)[0]
    assert insert.warning_html.startswith("<p>")
    assert "警語" in insert.warning_html


def test_sections_nested(xml_bytes):
    insert = parse_get_drug_doc(xml_bytes)[0]
    titles_l1 = [s.title for s in insert.sections]
    assert titles_l1 == [
        "性狀",
        "適應症",
        "用法及用量",
        "警語及注意事項",
        "副作用/不良反應",
        "特殊族群之用藥",
        "過量",
        "藥理特性",
        "臨床試驗資料",
        "藥品保存",
        "病人使用須知",
        "其他",
        "未來新欄位",
    ]

    xingzhuang = insert.sections[0]
    assert [c.title for c in xingzhuang.children] == ["有效成分及含量", "賦形劑", "藥品外觀"]

    ingredient_subsection = xingzhuang.children[0]
    assert "Amlodipine besylate" in ingredient_subsection.text
    assert ingredient_subsection.text.startswith("<p>")


def test_level1_section_with_direct_value(xml_bytes):
    insert = parse_get_drug_doc(xml_bytes)[0]
    indication = insert.sections[1]
    assert indication.title == "適應症"
    assert "高血壓" in indication.text


def test_image_value_is_captured_not_skipped(xml_bytes):
    """`<VALUE type="image" encode="1">` is captured as an InsertImage.

    Section text stays empty (an image is not text), but the base64 payload,
    mime type, and decoded size are retained so the wrapper can surface image
    metadata (and, on response_format="full", the data_url).
    """
    insert = parse_get_drug_doc(xml_bytes)[0]
    appearance = next(c for c in insert.sections[0].children if c.number == "1.4")
    assert appearance.title == "藥品外觀"
    assert appearance.text == ""
    assert len(appearance.images) == 1
    img = appearance.images[0]
    assert img.mime == "image/jpeg"
    assert img.data == "QUJDREVG"  # base64 for "ABCDEF"
    assert img.size_bytes == 6  # noqa: PLR2004 — len(b"ABCDEF")


def test_factories_and_companies(xml_bytes):
    insert = parse_get_drug_doc(xml_bytes)[0]
    assert len(insert.main_factory) == 1
    assert insert.main_factory[0]["name"] == "久裕企業股份有限公司"
    assert insert.main_factory[0]["number"] == "1"

    assert len(insert.sub_factories) == 1
    assert insert.sub_factories[0]["name"] == "輝瑞愛爾蘭製藥廠"
    assert insert.sub_factories[0]["number"] == "2"

    assert len(insert.companies) == 2  # noqa: PLR2004
    assert insert.companies[0]["name"] == "暉致醫藥股份有限公司"
    assert insert.companies[1]["name"] == "VIATRIS PHARMACEUTICALS LLC"


def test_image_mime_falls_back_to_filename_extension():
    """When the image VALUE omits `mimetype` (observed live), mime is inferred from the filename."""
    xml = """<?xml version="1.0" encoding="utf-8"?>
<ROOTDOCUMENT><DOCUMENT>
  <INFO><SNO>X</SNO><CNAME>測試</CNAME><ENAME>T</ENAME><DTYPE>須由醫師處方使用</DTYPE>
    <VERSION>1</VERSION><VDATE>2026-01-01</VDATE></INFO>
  <CONTENT>
    <SECTION LEVEL="1" ID="1"><NO>1</NO><TITLE>性狀</TITLE>
      <SECTION LEVEL="2" ID="1.4"><NO>1.4</NO><TITLE>外觀</TITLE>
        <VALUE type="image" encode="1" filename="pill.PNG">QUJD</VALUE>
      </SECTION>
    </SECTION>
    <SECTION LEVEL="1" ID="2"><NO>2</NO><TITLE>適應症</TITLE>
      <VALUE type="image" encode="1">QUJD</VALUE>
    </SECTION>
  </CONTENT>
</DOCUMENT></ROOTDOCUMENT>""".encode()
    insert = parse_get_drug_doc(xml)[0]
    by_no = {s.number: s for s in insert.sections[0].children}
    assert by_no["1.4"].images[0].mime == "image/png"  # from .PNG extension
    # No mimetype and no filename → generic fallback (never a malformed data: URL).
    assert insert.sections[1].images[0].mime == "application/octet-stream"


def test_image_mime_sniffs_magic_bytes_when_attrs_absent():
    """Real TFDA images omit mimetype AND filename; magic bytes must resolve the type."""
    jpeg_b64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 12).decode()
    png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).decode()
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
    <ROOTDOCUMENT><DOCUMENT>
      <INFO><SNO>X</SNO><CNAME>c</CNAME><ENAME>e</ENAME>
            <DTYPE>d</DTYPE><VERSION>v</VERSION><VDATE>2026/01/01</VDATE></INFO>
      <CONTENT>
        <SECTION LEVEL="1"><NO>1</NO><TITLE>性狀</TITLE>
          <VALUE type="image" encode="1">{jpeg_b64}</VALUE></SECTION>
        <SECTION LEVEL="1"><NO>2</NO><TITLE>x</TITLE>
          <VALUE type="image" encode="1">{png_b64}</VALUE></SECTION>
      </CONTENT>
    </DOCUMENT></ROOTDOCUMENT>""".encode()

    inserts = parse_get_drug_doc(xml)
    by_no = {s.number: s for s in inserts[0].sections}
    assert by_no["1"].images[0].mime == "image/jpeg"
    assert by_no["2"].images[0].mime == "image/png"


def test_empty_root_returns_empty_list():
    inserts = parse_get_drug_doc(b'<?xml version="1.0"?><ROOTDOCUMENT />')
    assert inserts == []


def test_malformed_xml_raises():
    with pytest.raises(InsertParseError):
        parse_get_drug_doc(b"not xml")


def test_error_element_raises():
    error_xml = '<?xml version="1.0"?><Error><Message>許可證代碼長度錯誤</Message></Error>'.encode()
    with pytest.raises(InsertParseError) as exc_info:
        parse_get_drug_doc(error_xml)
    assert "許可證代碼長度錯誤" in exc_info.value.message
