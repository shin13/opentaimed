# path: src/taiwan_fda_mcp/sources/opendata/search.py
# brief: Flat multi-field AND search over Dataset 37 with license-row collapse.

from dataclasses import dataclass

from taiwan_fda_mcp.models import DrugLicense

# License-prefix authority for tie-breaking when many licenses share an ingredient.
# Lower rank = higher authority (more likely the canonical brand reference).
_PREFIX_AUTHORITY: dict[str, int] = {
    "衛署藥輸": 0,
    "衛部藥輸": 0,
    "衛部菌疫輸": 1,
    "衛部罕藥製": 2,
    "衛署藥製": 3,
    "衛部藥製": 3,
    "內衛藥製": 4,
}


@dataclass
class LicenseGroup:
    """One distinct license_no after collapsing duplicate-manufacturer rows."""

    license: DrugLicense  # representative row (first seen for this license_no)
    manufacturers: list[str]  # all distinct, non-empty manufacturers for this license


def _authority_rank(license_no: str) -> int:
    """Map a license_no to its authority rank; unknown prefixes sort last (99)."""
    for prefix, rank in _PREFIX_AUTHORITY.items():
        if license_no.startswith(prefix):
            return rank
    return 99


def _collapse(licenses: list[DrugLicense]) -> list[LicenseGroup]:
    """Group rows by license_no, preserving first-seen order and merging manufacturers."""
    groups: dict[str, LicenseGroup] = {}
    for row in licenses:
        g = groups.get(row.license_no)
        if g is None:
            groups[row.license_no] = LicenseGroup(
                license=row,
                manufacturers=[row.manufacturer] if row.manufacturer else [],
            )
        elif row.manufacturer and row.manufacturer not in g.manufacturers:
            g.manufacturers.append(row.manufacturer)
    return list(groups.values())


def _matches(
    group: LicenseGroup,
    *,
    query: str,
    substr_filters: dict[str, str],
    manufacturer: str,
    country: str,
) -> bool:
    lic = group.license
    if query:
        hay = " ".join([lic.name_zh, lic.name_en, lic.ingredient, lic.license_no]).lower()
        if query.lower() not in hay:
            return False
    for field_name, needle in substr_filters.items():
        if not needle:
            continue
        value = getattr(lic, field_name) or ""  # drug_class may be None
        if needle.lower() not in value.lower():
            return False
    if manufacturer and not any(
        manufacturer.lower() in m.lower() for m in group.manufacturers
    ):
        return False
    if country:
        return country.lower() == (lic.country or "").lower()
    return True


def search_drugs(
    licenses: list[DrugLicense],
    *,
    query: str = "",
    name_zh: str = "",
    name_en: str = "",
    ingredient: str = "",
    indication: str = "",
    applicant: str = "",
    manufacturer: str = "",
    form: str = "",
    drug_class: str = "",
    country: str = "",
    limit: int = 10,
) -> tuple[int, list[LicenseGroup]]:
    """Filter Dataset 37 by flat AND-combined criteria; collapse rows by license_no.

    Free-text fields (query + name/ingredient/indication/applicant/manufacturer/
    form/drug_class) match by case-insensitive substring; `country` matches by
    case-insensitive exact. `query` is OR-across name_zh+name_en+ingredient+license_no.
    Collapsing happens before truncation, so `total` is the distinct-license count
    and `limit` truncates licenses, not raw rows.

    Returns:
        (total_matched, results) — total is the un-truncated distinct-license
        match count; results is authority-sorted and truncated to `limit`.
    """
    substr_filters = {
        "name_zh": name_zh.strip(),
        "name_en": name_en.strip(),
        "ingredient": ingredient.strip(),
        "indication": indication.strip(),
        "applicant": applicant.strip(),
        "form": form.strip(),
        "drug_class": drug_class.strip(),
    }
    query = query.strip()
    manufacturer = manufacturer.strip()
    country = country.strip()
    if not query and not country and not manufacturer and not any(substr_filters.values()):
        return 0, []

    matches = [
        g
        for g in _collapse(licenses)
        if _matches(
            g,
            query=query,
            substr_filters=substr_filters,
            manufacturer=manufacturer,
            country=country,
        )
    ]
    matches.sort(key=lambda g: (_authority_rank(g.license.license_no), g.license.name_zh))
    return len(matches), matches[:limit]
