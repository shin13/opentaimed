# path: tests/unit/test_packaging.py
# brief: Guard the published packaging contract — console-script entry points so
#        `uvx`/`pip` installs keep launching the server, and the reported
#        __version__ so it cannot drift from the distribution again.

from importlib.metadata import entry_points, version

import pytest

import taiwan_fda_mcp

DISTRIBUTION = "taiwan-fda-mcp"
SOURCE_TREE_FALLBACK = "0.0.0+unknown"

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


def test_version_is_read_from_distribution_metadata():
    """`__version__` reports the installed distribution version, not a fallback.

    Asserting `__version__ == version(DISTRIBUTION)` alone would be tautological,
    since that call *is* the implementation. The load-bearing assertion is the
    first one: in any properly installed environment — CI, the Docker image,
    a `uvx`/`pip` install — the source-tree fallback must NOT be what consumers
    see. That fails if the distribution is renamed in pyproject.toml without
    updating this package, or if packaging breaks such that no metadata is
    installed alongside the importable module.
    """
    assert taiwan_fda_mcp.__version__ != SOURCE_TREE_FALLBACK
    assert taiwan_fda_mcp.__version__ == version(DISTRIBUTION)
