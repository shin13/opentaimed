# path: tests/unit/test_dataset42.py
# brief: Verify Dataset 42 (drug appearance) parse + cache behaviour.

from pathlib import Path

from taiwan_fda_mcp.models import DrugAppearance


def test_drug_appearance_defaults_empty():
    a = DrugAppearance(license_no="內衛成製字第000075號")
    assert a.name_zh == ""
    assert a.shape == ""
    assert a.imprint_2 == ""
    assert a.image_url == ""
