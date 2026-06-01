# path: tests/unit/test_packaging.py
# brief: Guard the published console-script entry points so `uvx`/`pip` installs
#        keep launching the server; a renamed main() or dropped script regresses.

from importlib.metadata import entry_points

import pytest

# `taiwan-fda-mcp` matches the package name so `uvx taiwan-fda-mcp` works
# (uvx resolves a bare command to the package of the same name); `-server`
# is the backward-compatible alias.
CONSOLE_SCRIPTS = ("taiwan-fda-mcp", "taiwan-fda-mcp-server")
EXPECTED_TARGET = "taiwan_fda_mcp.mcp_server:main"


@pytest.mark.parametrize("script", CONSOLE_SCRIPTS)
def test_console_script_is_declared(script):
    """Each documented console script is declared and points at main().

    `uvx taiwan-fda-mcp` and a client's MCP config rely on this contract.
    Renaming or dropping an entry point silently breaks every install.
    """
    scripts = {e.name: e.value for e in entry_points(group="console_scripts")}
    assert script in scripts
    assert scripts[script] == EXPECTED_TARGET


@pytest.mark.parametrize("script", CONSOLE_SCRIPTS)
def test_console_script_loads_to_callable(script):
    """Each entry point resolves to a callable (the stdio server boot)."""
    (ep,) = (e for e in entry_points(group="console_scripts") if e.name == script)
    assert callable(ep.load())
