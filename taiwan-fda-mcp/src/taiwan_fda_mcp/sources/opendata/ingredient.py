# path: src/taiwan_fda_mcp/sources/opendata/ingredient.py
# brief: Group Dataset 37 licenses by verbatim 主成分略述 signature (no normalization).

from dataclasses import dataclass

from taiwan_fda_mcp.models import DrugLicense
from taiwan_fda_mcp.sources.opendata.search import (
    LicenseGroup,
    _authority_rank,
    _collapse,
)

# The SOLE real combination-product delimiter in Dataset 37. Empirically, '+' and
# ',' occur only INSIDE single chemical names ("SENNOSIDE A+B", "GADOXETIC ACID,
# DISODIUM SALT"), never between two active ingredients — so splitting on ';;'
# alone classifies mono vs combo exactly, with no dictionary required.
_COMBO_DELIMITER = ";;"


def signature(ingredient: str) -> tuple[str, ...]:
    """Split 主成分略述 into a sorted, verbatim component signature.

    Splits on ';;' only. Each component is whitespace-trimmed but otherwise
    UNCHANGED — salt forms are preserved and nothing is normalized. So
    'AMLODIPINE BESYLATE' and 'AMLODIPINE BESILATE' produce distinct signatures,
    faithful to exactly how each license is registered. Sorting makes the
    signature order-independent ('A;;B' and 'B;;A' collapse to one group).

    This is the single swappable seam. To later merge salt-form variants
    (the deferred "Option 3" normalization), normalize each component here
    before sorting — no caller, response model, or snapshot needs to change.

    Args:
        ingredient: raw 主成分略述 field value.

    Returns:
        Sorted tuple of trimmed component strings; empty tuple if blank.
    """
    parts = [p.strip() for p in ingredient.split(_COMBO_DELIMITER) if p.strip()]
    return tuple(sorted(parts))


@dataclass
class IngredientSignatureGroup:
    """Licenses sharing one identical 主成分略述 signature.

    Internal (dataclass) counterpart to the public Pydantic `IngredientGroup`;
    the tool adapter maps this to the wire model, mirroring how `LicenseGroup`
    maps to `DrugLicenseRow`.
    """

    components: tuple[str, ...]  # sorted verbatim components; len 1 ⇒ mono
    is_mono: bool
    licenses: list[LicenseGroup]  # distinct license_no, authority-sorted


def group_by_ingredient(licenses: list[DrugLicense]) -> list[IngredientSignatureGroup]:
    """Collapse rows by license_no, then group by verbatim ingredient signature.

    The caller is expected to pass a set already filtered to the ingredient of
    interest (substring matching stays in the search layer); this function only
    groups whatever it is given.

    Groups are sorted 單方-first (`is_mono` desc), then by descending license
    count, then by `components`. Within each group, licenses are authority-sorted
    (import/原廠 first) then by `name_zh`, mirroring `search_drugs`. Rows whose
    主成分略述 is blank are dropped (no signature to group on).

    Args:
        licenses: Dataset 37 rows (may contain duplicate-manufacturer rows per
            license; they are collapsed here).

    Returns:
        Signature groups in display order.
    """
    buckets: dict[tuple[str, ...], list[LicenseGroup]] = {}
    for group in _collapse(licenses):
        sig = signature(group.license.ingredient)
        if not sig:
            continue
        buckets.setdefault(sig, []).append(group)

    result = [
        IngredientSignatureGroup(
            components=sig,
            is_mono=len(sig) == 1,
            licenses=sorted(
                members,
                key=lambda g: (_authority_rank(g.license.license_no), g.license.name_zh),
            ),
        )
        for sig, members in buckets.items()
    ]
    result.sort(key=lambda grp: (not grp.is_mono, -len(grp.licenses), grp.components))
    return result
