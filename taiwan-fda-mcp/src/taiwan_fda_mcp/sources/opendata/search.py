# path: src/taiwan_fda_mcp/sources/opendata/search.py
# brief: Substring search across DrugLicense fields.

from typing import Literal

from taiwan_fda_mcp.models import DrugLicense

SearchField = Literal["any", "name_zh", "name_en", "ingredient", "license_no"]


def search_drugs(
    licenses: list[DrugLicense],
    keyword: str,
    *,
    search_by: SearchField = "any",
    limit: int = 50,
) -> list[DrugLicense]:
    """Return licenses where keyword appears (case-insensitive).

    Dataset 37 is the 「未註銷藥品許可證資料集」 — cancelled rows do not exist
    in upstream data, so this function does not filter on cancel_status.

    Args:
        licenses: full Dataset 37 list.
        keyword: search term (whitespace-stripped, lowercased internally).
        search_by: which field(s) to search. "any" = name_zh + name_en + ingredient + license_no.
        limit: maximum results returned.

    Returns:
        Matching DrugLicense rows sorted by name_zh, truncated to limit.
    """
    keyword = keyword.strip().lower()
    if not keyword:
        return []

    matches: list[DrugLicense] = []
    for row in licenses:
        haystack = _haystack(row, search_by).lower()
        if keyword in haystack:
            matches.append(row)

    matches.sort(key=lambda r: r.name_zh)
    return matches[:limit]


def _haystack(row: DrugLicense, search_by: SearchField) -> str:
    if search_by == "any":
        return " ".join([row.name_zh, row.name_en, row.ingredient, row.license_no])
    if search_by == "name_zh":
        return row.name_zh
    if search_by == "name_en":
        return row.name_en
    if search_by == "ingredient":
        return row.ingredient
    if search_by == "license_no":
        return row.license_no
    return ""
