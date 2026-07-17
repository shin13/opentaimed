# path: tests/unit/test_dataset42.py
# brief: Verify Dataset 42 (drug appearance) parse + cache behaviour.

import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from taiwan_fda_mcp.models import DrugAppearance
from taiwan_fda_mcp.sources.opendata.client import fetch_dataset42
from taiwan_fda_mcp.sources.opendata.dataset42 import (
    load_from_cache,
    parse_rows,
    write_to_cache,
)


def _zip_json(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("42_5.json", json.dumps(rows, ensure_ascii=False))
    return buf.getvalue()


def test_drug_appearance_defaults_empty():
    a = DrugAppearance(license_no="內衛成製字第000075號")
    assert a.name_zh == ""
    assert a.shape == ""
    assert a.imprint_2 == ""
    assert a.image_url == ""


def test_parse_rows_maps_chinese_keys_and_coerces_scalars():
    raw = [
        {
            "許可證字號": "內衛成製字第000075號",
            "中文品名": '"福元"蘇打錠500毫克',
            "英文品名": "SODIUM BICARBONATE",
            "形狀": "圓形",
            "顏色": "白",
            "外觀尺寸": 8,  # numeric in source JSON
            "標註一": "FY T061",
            "標註二": None,  # null in source JSON
            "外觀圖檔連結": "https://mcp.fda.gov.tw/insert/shapeImg/abc?c=o",
        }
    ]
    rows = parse_rows(raw)
    assert len(rows) == 1
    r = rows[0]
    assert r.shape == "圓形"
    assert r.dimensions == "8"  # coerced to str
    assert r.imprint_2 == ""  # None → ""
    assert r.image_url.endswith("?c=o")


def test_cache_round_trip(tmp_path: Path):
    rows = parse_rows([{"許可證字號": "L1", "中文品名": "藥", "形狀": "圓形"}])
    write_to_cache(rows, tmp_path)
    loaded = load_from_cache(tmp_path)
    assert loaded is not None
    assert loaded[0].license_no == "L1"
    assert loaded[0].shape == "圓形"


def test_load_from_cache_missing_returns_none(tmp_path: Path):
    assert load_from_cache(tmp_path) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_dataset42_downloads_and_parses():
    rows = [{"許可證字號": "L1", "中文品名": "藥", "形狀": "圓形"}]
    respx.get("https://data.fda.gov.tw/data/opendata/export/42/json").mock(
        return_value=httpx.Response(200, content=_zip_json(rows))
    )
    result = await fetch_dataset42("https://data.fda.gov.tw", rate_limit_interval=0.0)
    assert len(result) == 1
    assert result[0].shape == "圓形"
