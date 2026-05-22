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
    results = search_drugs(licenses, keyword="脈優")
    assert len(results) == 1
    assert results[0].name_zh == "脈優錠５毫克"


def test_search_any_by_en_name_case_insensitive(licenses):
    results = search_drugs(licenses, keyword="lipitor")
    assert len(results) == 1
    assert results[0].name_en == "LIPITOR TABLETS 20MG"


def test_search_any_by_ingredient_returns_multiple(licenses):
    results = search_drugs(licenses, keyword="atorvastatin")
    assert len(results) == 2  # noqa: PLR2004
    names = {r.name_zh for r in results}
    assert names == {"立普妥膜衣錠20毫克", "泰脂膜衣錠20毫克"}


def test_search_by_specific_field(licenses):
    # "atorvastatin" appears in ingredient but not in name_zh
    assert search_drugs(licenses, keyword="atorvastatin", search_by="name_zh") == []
    assert len(search_drugs(licenses, keyword="atorvastatin", search_by="ingredient")) == 2  # noqa: PLR2004


def test_search_by_license_no(licenses):
    results = search_drugs(licenses, keyword="021571", search_by="license_no")
    assert len(results) == 1
    assert results[0].license_no == "衛署藥輸字第021571號"


def test_limit(licenses):
    results = search_drugs(licenses, keyword="atorvastatin", limit=1)
    assert len(results) == 1


def test_empty_keyword_returns_empty(licenses):
    assert search_drugs(licenses, keyword="") == []
    assert search_drugs(licenses, keyword="   ") == []
