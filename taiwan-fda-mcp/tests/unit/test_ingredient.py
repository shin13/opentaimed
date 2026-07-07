# path: tests/unit/test_ingredient.py
# brief: Verify ingredient signature parsing and verbatim (no-normalization) grouping.

import json
from pathlib import Path

import pytest

from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows
from taiwan_fda_mcp.sources.opendata.ingredient import group_by_ingredient, signature


@pytest.fixture
def licenses(fixtures_dir: Path):
    raw = json.loads(
        (fixtures_dir / "dataset37_ingredient_sample.json").read_text(encoding="utf-8")
    )
    return parse_rows(raw)


# --- signature(): the swappable, verbatim group key -------------------------


def test_signature_mono_is_single_element():
    assert signature("AMLODIPINE BESYLATE") == ("AMLODIPINE BESYLATE",)


def test_signature_splits_on_double_semicolon():
    assert signature("AMLODIPINE BESYLATE;;VALSARTAN") == ("AMLODIPINE BESYLATE", "VALSARTAN")


def test_signature_is_order_independent():
    assert signature("A;;B") == signature("B;;A") == ("A", "B")


def test_signature_preserves_salt_form_verbatim():
    # besylate vs besilate are NOT merged — distinct signatures by design.
    assert signature("AMLODIPINE BESYLATE") != signature("AMLODIPINE BESILATE")


def test_signature_does_not_split_on_plus():
    # '+' is part of a single chemical name ("SENNOSIDE A+B"), not a combo delimiter.
    assert signature("SENNOSIDE A+B") == ("SENNOSIDE A+B",)


def test_signature_does_not_split_on_comma():
    # ',' occurs inside a single ingredient's salt qualifier, not between ingredients.
    assert signature("GADOXETIC ACID, DISODIUM SALT") == ("GADOXETIC ACID, DISODIUM SALT",)


def test_signature_blank_is_empty_tuple():
    assert signature("") == ()
    assert signature("   ") == ()


# --- group_by_ingredient(): grouping, sorting, counts -----------------------


def _amlodipine(licenses):
    """Mirror the search layer's substring filter, then group."""
    matched = [lic for lic in licenses if "amlodipine" in lic.ingredient.lower()]
    return group_by_ingredient(matched)


def test_mono_splits_by_salt_spelling(licenses):
    groups = _amlodipine(licenses)
    mono = [g for g in groups if g.is_mono]
    # Three mono spellings each form their own group (deliberate "faithful split").
    assert {g.components for g in mono} == {
        ("AMLODIPINE (BESYLATE)",),
        ("AMLODIPINE BESILATE",),
        ("AMLODIPINE BESYLATE",),
    }


def test_combo_order_independent_merge(licenses):
    groups = _amlodipine(licenses)
    valsartan_besylate = [
        g for g in groups if g.components == ("AMLODIPINE BESYLATE", "VALSARTAN")
    ]
    # 脈力穩 (A;;VALSARTAN) and 安普新 (VALSARTAN;;A) collapse into ONE group.
    assert len(valsartan_besylate) == 1
    assert len(valsartan_besylate[0].licenses) == 2  # noqa: PLR2004


def test_combo_salt_spelling_fragments(licenses):
    groups = _amlodipine(licenses)
    sigs = {g.components for g in groups if not g.is_mono}
    # besylate+valsartan and besilate+valsartan stay DISTINCT (the wanted 假分裂).
    assert ("AMLODIPINE BESYLATE", "VALSARTAN") in sigs
    assert ("AMLODIPINE BESILATE", "VALSARTAN") in sigs


def test_edge_case_rows_excluded_by_filter(licenses):
    # SENNOSIDE A+B and GADOXETIC ACID, ... contain no 'amlodipine' → never grouped.
    groups = _amlodipine(licenses)
    all_components = {c for g in groups for c in g.components}
    assert not any("SENNOSIDE" in c or "GADOXETIC" in c for c in all_components)


def test_group_ordering_mono_first_then_count_desc(licenses):
    groups = _amlodipine(licenses)
    # All three mono groups precede every combo group.
    first_combo = next(i for i, g in enumerate(groups) if not g.is_mono)
    assert all(groups[i].is_mono for i in range(first_combo))
    # The 2-license combo (besylate+valsartan) leads the combo section.
    assert groups[first_combo].components == ("AMLODIPINE BESYLATE", "VALSARTAN")
    assert len(groups[first_combo].licenses) == 2  # noqa: PLR2004


def test_within_group_authority_sort_imports_first(licenses):
    groups = _amlodipine(licenses)
    combo = next(g for g in groups if g.components == ("AMLODIPINE BESYLATE", "VALSARTAN"))
    # 安普新 is 衛署藥輸 (import, rank 0); 脈力穩 is 衛部藥製 (rank 3) → import leads.
    assert combo.licenses[0].license.name_zh == "安普新膜衣錠5/80毫克"


def test_counts_are_consistent(licenses):
    groups = _amlodipine(licenses)
    mono_licenses = sum(len(g.licenses) for g in groups if g.is_mono)
    combo_licenses = sum(len(g.licenses) for g in groups if not g.is_mono)
    assert mono_licenses == 3  # noqa: PLR2004  three mono spellings, 1 each
    assert combo_licenses == 4  # noqa: PLR2004  besylate+val (2), besilate+val (1), olme (1)
    assert mono_licenses + combo_licenses == 7  # noqa: PLR2004  distinct amlodipine licenses
