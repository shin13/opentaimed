# path: src/taiwan_fda_mcp/sources/insert/html_text.py
# brief: Convert FDA insert HTML fragments to plain text (stdlib only).

from html.parser import HTMLParser
from io import StringIO

# Block-level tags that introduce a line break in the rendered output.
_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})
# Tags whose content should be dropped entirely (style/script never legitimate here, but defensive).
_DROP_TAGS = frozenset({"script", "style"})


class _HTMLTextExtractor(HTMLParser):
    """Stdlib HTMLParser subclass — strip tags, decode entities, preserve paragraph breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # `&amp;` → `&` automatically
        self._buf = StringIO()
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs  # HTMLParser interface — we don't need attribute values.
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._buf.write("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._buf.write("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self._buf.write(data)

    def get_text(self) -> str:
        return self._buf.getvalue()


def html_to_text(html: str) -> str:
    """Convert an HTML fragment from FDA inserts into normalised plain text.

    - Tags stripped, HTML entities decoded.
    - Block-level tags (`<p>`, `<br>`, `<li>`, table rows, headings) produce line breaks.
    - Inline tags (`<span>`, `<i>`, `<strong>`, ...) leave text in place.
    - Consecutive whitespace within a line is collapsed; runs of blank lines collapse to one.
    """
    if not html:
        return ""
    p = _HTMLTextExtractor()
    p.feed(html)
    p.close()
    raw = p.get_text()

    # Normalise: collapse intra-line whitespace, then collapse blank-line runs.
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    out: list[str] = []
    blank_prev = False
    for line in lines:
        if line:
            out.append(line)
            blank_prev = False
        elif not blank_prev:
            out.append("")
            blank_prev = True
    return "\n".join(out).strip()
