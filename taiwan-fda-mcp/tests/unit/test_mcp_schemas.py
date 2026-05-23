# path: tests/unit/test_mcp_schemas.py
# brief: Snapshot tests freezing the MCP input/output JSON schemas for all 3 tools.
#
# These tests serve as the contract between this server and its LLM clients.
# A schema change — even something subtle like a docstring rewording — breaks
# these tests and forces an explicit decision: was the change intentional?
#
# To regenerate snapshots after an intentional schema change:
#   UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_schemas.py
#
# Then commit the updated tests/snapshots/*.json files alongside the code change.

import json
import os
from pathlib import Path

import pytest
from fastmcp import Client

from taiwan_fda_mcp.mcp_server import mcp

_SNAPSHOTS_DIR = Path(__file__).parent.parent / "snapshots"
_UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"

TOOL_NAMES = ["search_drugs", "get_package_insert", "check_insert_updates"]


async def _fetch_schemas() -> dict[str, dict[str, dict | None]]:
    """Return {tool_name: {'input': inputSchema, 'output': outputSchema}}."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
    return {t.name: {"input": t.inputSchema, "output": t.outputSchema} for t in tools}


def _snapshot_path(tool: str, kind: str) -> Path:
    return _SNAPSHOTS_DIR / f"{tool}.{kind}.json"


def _assert_snapshot(actual: dict | None, tool: str, kind: str) -> None:
    """Compare `actual` schema against the stored JSON snapshot.

    First-run / UPDATE_SNAPSHOTS=1: write the file.
    Subsequent runs: assert byte-equal serialisation.
    """
    path = _snapshot_path(tool, kind)
    serialised = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if _UPDATE or not path.exists():
        _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(serialised, encoding="utf-8")
        if not _UPDATE:
            pytest.skip(f"Wrote initial snapshot {path.name}; re-run to verify.")
        return

    expected = path.read_text(encoding="utf-8")
    if expected != serialised:
        diff_hint = (
            f"\nSchema for {tool}.{kind} changed.\n"
            f"If intentional: UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_schemas.py\n"
            f"  Snapshot file: {path}\n"
        )
        # Show first few lines of diff for quick triage.
        expected_lines = expected.splitlines()
        actual_lines = serialised.splitlines()
        for i, (a, b) in enumerate(zip(expected_lines, actual_lines, strict=False)):
            if a != b:
                diff_hint += f"  First diff at line {i + 1}:\n    - {a}\n    + {b}\n"
                break
        pytest.fail(diff_hint)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOL_NAMES)
async def test_input_schema_snapshot(tool: str) -> None:
    schemas = await _fetch_schemas()
    _assert_snapshot(schemas[tool]["input"], tool, "input")


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOL_NAMES)
async def test_output_schema_snapshot(tool: str) -> None:
    schemas = await _fetch_schemas()
    _assert_snapshot(schemas[tool]["output"], tool, "output")


@pytest.mark.asyncio
async def test_all_tools_have_output_schemas() -> None:
    """Every tool must declare its output shape — this is the LLM's only signal
    of what fields to expect in the response. Missing outputSchema = LLM has to
    guess and will hallucinate field names.
    """
    schemas = await _fetch_schemas()
    for name, s in schemas.items():
        assert s["output"] is not None, f"{name} has no outputSchema — declare a Pydantic return type"
