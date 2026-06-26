# path: tests/unit/test_config.py
# brief: Verify Settings defaults — notably the per-user OS cache dir (uvx-safe).

import pytest
from pydantic import ValidationError

from taiwan_fda_mcp.config import Settings, get_settings


def test_cache_dir_defaults_to_user_cache_not_cwd(monkeypatch):
    """Default cache dir must resolve under the per-user OS cache dir, not cwd.

    uvx installs are ephemeral/read-only; a cwd-relative `.cache/...` default
    would be lost between runs (or unwritable). The default must live in the
    platform user cache dir under a `taiwan-fda-mcp` namespace.
    """
    monkeypatch.delenv("DATASET37_CACHE_DIR", raising=False)
    s = get_settings()
    cache_dir = str(s.DATASET37_CACHE_DIR)
    assert "taiwan-fda-mcp" in cache_dir
    # not the old cwd-relative default
    assert not cache_dir.startswith(".cache")


def test_insert_throttle_interval_default(monkeypatch):
    """Safe-by-default: the insert egress throttle defaults to 0.5s."""
    monkeypatch.delenv("INSERT_THROTTLE_MIN_INTERVAL_SECONDS", raising=False)
    s = get_settings()
    assert s.INSERT_THROTTLE_MIN_INTERVAL_SECONDS == 0.5  # noqa: PLR2004


def test_insert_throttle_interval_env_override(monkeypatch):
    """Operators of the shared HTTP service tune the interval via env."""
    monkeypatch.setenv("INSERT_THROTTLE_MIN_INTERVAL_SECONDS", "1.5")
    s = get_settings()
    assert s.INSERT_THROTTLE_MIN_INTERVAL_SECONDS == 1.5  # noqa: PLR2004


def test_transport_defaults_to_stdio():
    # Assert the field default directly so a local .env cannot affect the result.
    assert Settings.model_fields["MCP_TRANSPORT"].default == "stdio"
    assert Settings.model_fields["MCP_HTTP_HOST"].default == "127.0.0.1"
    assert Settings.model_fields["MCP_HTTP_PORT"].default == 8765  # noqa: PLR2004
    assert Settings.model_fields["MCP_HTTP_PATH"].default == "/mcp/"


def test_invalid_transport_rejected_at_load():
    # fail-fast: a typo'd transport must raise at settings construction,
    # never mid-request.
    with pytest.raises(ValidationError):
        Settings(MCP_TRANSPORT="banana")  # type: ignore[arg-type]
