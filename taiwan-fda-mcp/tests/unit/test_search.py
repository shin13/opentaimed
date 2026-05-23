# path: tests/unit/test_search.py
# brief: Verify in-memory drug search behaviour.

import json
from pathlib import Path

import pytest

from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows
from taiwan_fda_mcp.sources.opendata.search import search_drugs


@pytest.fixture
def licenses(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    return parse_rows(raw)


def test_search_any_by_zh_name(licenses):
    total, results = search_drugs(licenses, keyword="脈優")
    assert total == 1
    assert len(results) == 1
    assert results[0].name_zh == "脈優錠５毫克"


def test_search_any_by_en_name_case_insensitive(licenses):
    total, results = search_drugs(licenses, keyword="lipitor")
    assert total == 1
    assert results[0].name_en == "LIPITOR TABLETS 20MG"


def test_search_any_by_ingredient_returns_multiple(licenses):
    total, results = search_drugs(licenses, keyword="atorvastatin")
    assert total == 2  # noqa: PLR2004
    names = {r.name_zh for r in results}
    assert names == {"立普妥膜衣錠20毫克", "泰脂膜衣錠20毫克"}


def test_search_by_specific_field(licenses):
    total_n, _ = search_drugs(licenses, keyword="atorvastatin", search_by="name_zh")
    assert total_n == 0
    total_i, results_i = search_drugs(licenses, keyword="atorvastatin", search_by="ingredient")
    assert total_i == 2  # noqa: PLR2004
    assert len(results_i) == 2  # noqa: PLR2004


def test_search_by_license_no(licenses):
    total, results = search_drugs(licenses, keyword="021571", search_by="license_no")
    assert total == 1
    assert results[0].license_no == "衛署藥輸字第021571號"


def test_limit_truncates_results_but_keeps_total(licenses):
    """limit truncates the results list but `total` reflects the full match count."""
    total, results = search_drugs(licenses, keyword="atorvastatin", limit=1)
    assert total == 2  # full match count preserved  # noqa: PLR2004
    assert len(results) == 1  # but only 1 returned due to limit


def test_empty_keyword_returns_empty(licenses):
    assert search_drugs(licenses, keyword="") == (0, [])
    assert search_drugs(licenses, keyword="   ") == (0, [])


def test_authority_ranking_imports_first(licenses):
    """Import-prefix licenses (衛署藥輸 / 衛部藥輸) should outrank locally-made ones.

    For atorvastatin: 立普妥 (衛署藥輸 = brand import, rank 0) vs
    泰脂 (衛署藥製 = local manufacture, rank 3). 立普妥 must come first.
    """
    _, results = search_drugs(licenses, keyword="atorvastatin")
    assert results[0].name_zh == "立普妥膜衣錠20毫克"
    assert results[1].name_zh == "泰脂膜衣錠20毫克"
