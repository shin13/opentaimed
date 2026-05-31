# path: tests/unit/test_search.py
# brief: Verify in-memory drug search: flat AND filters, collapse, exact country.

import json
from pathlib import Path

import pytest

from taiwan_fda_mcp.models import DrugLicense
from taiwan_fda_mcp.sources.opendata.dataset37 import parse_rows
from taiwan_fda_mcp.sources.opendata.search import LicenseGroup, search_drugs


@pytest.fixture
def licenses(fixtures_dir: Path):
    raw = json.loads((fixtures_dir / "dataset37_sample.json").read_text(encoding="utf-8"))
    return parse_rows(raw)


def test_query_substring_on_zh_name(licenses):
    total, results = search_drugs(licenses, query="脈優")
    assert total == 1
    assert results[0].license.name_zh == "脈優錠５毫克"


def test_query_case_insensitive_en_name(licenses):
    total, results = search_drugs(licenses, query="lipitor")
    assert total == 1
    assert results[0].license.name_en == "LIPITOR TABLETS 20MG"


def test_query_by_ingredient_returns_two(licenses):
    total, results = search_drugs(licenses, query="atorvastatin")
    assert total == 2  # noqa: PLR2004
    assert {g.license.name_zh for g in results} == {"立普妥膜衣錠20毫克", "泰脂膜衣錠20毫克"}


def test_authority_ranking_imports_first(licenses):
    _, results = search_drugs(licenses, query="atorvastatin")
    assert results[0].license.name_zh == "立普妥膜衣錠20毫克"  # 衛部藥輸 rank 0
    assert results[1].license.name_zh == "泰脂膜衣錠20毫克"  # 衛部藥製 rank 3


def test_per_field_filter_name_zh(licenses):
    # ingredient text does not appear in name_zh → 0 results.
    total, _ = search_drugs(licenses, name_zh="atorvastatin")
    assert total == 0
    total, results = search_drugs(licenses, name_zh="立普妥")
    assert total == 1
    assert results[0].license.name_zh == "立普妥膜衣錠20毫克"


def test_filters_and_combine(licenses):
    # ingredient matches two, but name_zh narrows to one (AND).
    total, results = search_drugs(licenses, ingredient="atorvastatin", name_zh="泰脂")
    assert total == 1
    assert results[0].license.name_zh == "泰脂膜衣錠20毫克"


def test_country_exact_case_insensitive(licenses):
    total, results = search_drugs(licenses, country="tw")
    assert total == 2  # noqa: PLR2004  (泛星 + 泰脂, both "TW")
    assert all(g.license.country == "TW" for g in results)


def test_country_does_not_substring_overmatch(licenses):
    # "T" must NOT match "TW"/"IT" (exact, not substring).
    total, _ = search_drugs(licenses, country="T")
    assert total == 0


def test_no_criteria_returns_empty(licenses):
    # search.py treats "no criteria" as no matches; the tool layer turns this
    # into an explicit error (tested in test_tools.py).
    assert search_drugs(licenses) == (0, [])


def test_collapse_multi_manufacturer_into_one_row():
    rows = [
        DrugLicense(
            license_no="衛署藥輸字第021571號", name_zh="脈優錠５毫克",
            name_en="NORVASC", ingredient="AMLODIPINE", manufacturer="廠A",
        ),
        DrugLicense(
            license_no="衛署藥輸字第021571號", name_zh="脈優錠５毫克",
            name_en="NORVASC", ingredient="AMLODIPINE", manufacturer="廠B",
        ),
    ]
    total, results = search_drugs(rows, query="脈優")
    assert total == 1  # one license, not two rows
    assert isinstance(results[0], LicenseGroup)
    assert results[0].manufacturers == ["廠A", "廠B"]


def test_manufacturer_filter_matches_any_in_group():
    rows = [
        DrugLicense(
            license_no="衛署藥輸字第021571號", name_zh="脈優", name_en="N",
            ingredient="AMLODIPINE", manufacturer="廠A",
        ),
        DrugLicense(
            license_no="衛署藥輸字第021571號", name_zh="脈優", name_en="N",
            ingredient="AMLODIPINE", manufacturer="台灣藥廠",
        ),
    ]
    total, results = search_drugs(rows, manufacturer="台灣")
    assert total == 1
    assert "台灣藥廠" in results[0].manufacturers


def test_limit_truncates_groups_keeps_total(licenses):
    total, results = search_drugs(licenses, query="atorvastatin", limit=1)
    assert total == 2  # noqa: PLR2004
    assert len(results) == 1
