# path: tests/unit/test_packaging.py
# brief: Guard the published console-script entry point so `uvx`/`pip` installs
#        keep launching the server; a renamed main() or dropped script regresses.

from importlib.metadata import entry_points

CONSOLE_SCRIPT = "taiwan-fda-mcp-server"
EXPECTED_TARGET = "taiwan_fda_mcp.mcp_server:main"


def test_console_script_is_declared():
    """The distribution exposes the `taiwan-fda-mcp-server` console script.

    This is the contract `uvx taiwan-fda-mcp-server` / a client's MCP config
    relies on. Renaming the entry point silently breaks every install.
    """
    scripts = {e.name: e.value for e in entry_points(group="console_scripts")}
    assert CONSOLE_SCRIPT in scripts
    assert scripts[CONSOLE_SCRIPT] == EXPECTED_TARGET


def test_console_script_loads_to_callable():
    """The entry point resolves to a zero-arg callable (the stdio server boot).

    Guards against the target module/function going missing without the
    `[project.scripts]` declaration being updated.
    """
    (ep,) = (e for e in entry_points(group="console_scripts") if e.name == CONSOLE_SCRIPT)
    main = ep.load()
    assert callable(main)
