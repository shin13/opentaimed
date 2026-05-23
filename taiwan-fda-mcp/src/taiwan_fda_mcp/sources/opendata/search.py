# path: src/taiwan_fda_mcp/sources/opendata/search.py
# brief: Substring search across DrugLicense fields with authority-aware ranking.

from typing import Literal

from taiwan_fda_mcp.models import DrugLicense

SearchField = Literal["any", "name_zh", "name_en", "ingredient", "license_no"]

# License-prefix authority for tie-breaking when multiple licenses share an ingredient.
# Lower rank = higher authority (more likely the canonical brand reference).
#
# Reasoning:
#   - Import licenses (含「輸」字) are typically held by the original brand owner
#     (e.g. Norvasc, Lipitor) — treat as most authoritative.
#   - 罕藥製 = locally-manufactured rare drug, specialised → high authority.
#   - 藥製 = locally-manufactured generic → lower authority.
#   - 內衛 = legacy prefix → lowest.
_PREFIX_AUTHORITY: dict[str, int] = {
    "衛署藥輸": 0,
    "衛部藥輸": 0,
    "衛部菌疫輸": 1,
    "衛部罕藥製": 2,
    "衛署藥製": 3,
    "衛部藥製": 3,
    "內衛藥製": 4,
}


def _authority_rank(license_no: str) -> int:
    """Map a license_no like '衛署藥輸字第021571號' to its authority rank.

    Unknown prefixes get the worst rank (99) so they sort last.
    """
    for prefix, rank in _PREFIX_AUTHORITY.items():
        if license_no.startswith(prefix):
            return rank
    return 99


def search_drugs(
    licenses: list[DrugLicense],
    keyword: str,
    *,
    search_by: SearchField = "any",
    limit: int = 50,
) -> tuple[int, list[DrugLicense]]:
    """Return matches as (total_matched, truncated_sorted_list).

    Dataset 37 is the 「未註銷藥品許可證資料集」 — cancelled rows do not exist
    in upstream data, so this function does not filter on cancel_status.

    Sort order: (authority_rank ASC, name_zh ASC). This surfaces the most likely
    brand-reference license first when many generics share an ingredient.

    Args:
        licenses: full Dataset 37 list.
        keyword: search term (whitespace-stripped, lowercased internally).
        search_by: which field(s) to search. "any" = name_zh + name_en + ingredient + license_no.
        limit: maximum results returned (callers know the full count via the tuple's first item).

    Returns:
        (total_matched, results) — total is the un-truncated match count;
        results is the sorted, truncated list.
    """
    keyword = keyword.strip().lower()
    if not keyword:
        return 0, []

    matches: list[DrugLicense] = []
    for row in licenses:
        haystack = _haystack(row, search_by).lower()
        if keyword in haystack:
            matches.append(row)

    matches.sort(key=lambda r: (_authority_rank(r.license_no), r.name_zh))
    return len(matches), matches[:limit]


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
