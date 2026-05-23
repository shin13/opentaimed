# path: tests/unit/test_html_text.py
# brief: Verify FDA insert HTML → plain-text conversion.

from taiwan_fda_mcp.sources.insert.html_text import html_to_text


def test_strips_tags_and_decodes_entities() -> None:
    html = "<p>高血壓 &amp; 心絞痛</p>"
    assert html_to_text(html) == "高血壓 & 心絞痛"


def test_block_tags_produce_paragraph_breaks() -> None:
    html = "<p>Item 1</p><p>Item 2</p>"
    # Adjacent `<p>` blocks produce a blank line between them — readable structure.
    assert html_to_text(html) == "Item 1\n\nItem 2"


def test_inline_tags_kept_inline() -> None:
    html = "<p><strong>禁忌</strong>：對 <i>本藥</i> 過敏</p>"  # noqa: RUF001
    assert html_to_text(html) == "禁忌：對 本藥 過敏"  # noqa: RUF001


def test_list_items_separated() -> None:
    html = "<ul><li>活動性肝病</li><li>懷孕</li><li>哺乳</li></ul>"
    text = html_to_text(html)
    assert "活動性肝病" in text
    assert "懷孕" in text
    assert "哺乳" in text
    assert text.count("\n") >= 2  # noqa: PLR2004


def test_collapses_whitespace_and_blank_runs() -> None:
    html = "<p>   多餘空白   </p>\n\n\n<p>下一段</p>"
    # Many blank-line runs collapse to a single blank line.
    assert html_to_text(html) == "多餘空白\n\n下一段"


def test_drops_script_and_style() -> None:
    html = "<p>safe</p><script>alert(1)</script><style>p{}</style>"
    assert html_to_text(html) == "safe"


def test_empty_input() -> None:
    assert html_to_text("") == ""


def test_real_fda_warnings_fragment_shrinks_substantially() -> None:
    """Real insert warnings text is ~75% smaller after stripping styled tags."""
    html = (
        '<p style="text-align:justify;"><i><u>肌肉病變</u></i></p>'
        '<p style="text-align:justify;">Atorvastatin <strong>可能會造成肌肉病變</strong>'
        "(肌肉疼痛、壓痛或無力)。</p>"
    )
    text = html_to_text(html)
    assert "肌肉病變" in text
    assert "Atorvastatin" in text
    assert "可能會造成肌肉病變" in text
    assert "<p" not in text
    assert "style=" not in text
    assert len(text) < len(html) * 0.5
