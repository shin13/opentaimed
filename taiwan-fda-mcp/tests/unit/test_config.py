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


def test_insert_cache_settings_default_off(monkeypatch):
    """Cache is opt-in: defaults must leave it disabled with ADR-0011 sizing."""
    for key in (
        "INSERT_CACHE_ENABLED",
        "INSERT_CACHE_TTL_HOURS",
        "INSERT_CACHE_MAX_ENTRIES",
        "INSERT_CACHE_MAX_MB",
    ):
        monkeypatch.delenv(key, raising=False)
    s = Settings()
    assert s.INSERT_CACHE_ENABLED is False
    assert s.INSERT_CACHE_TTL_HOURS == 6.0  # noqa: PLR2004
    assert s.INSERT_CACHE_MAX_ENTRIES == 1000  # noqa: PLR2004
    assert s.INSERT_CACHE_MAX_MB == 128.0  # noqa: PLR2004


def test_insert_cache_enabled_from_env(monkeypatch):
    monkeypatch.setenv("INSERT_CACHE_ENABLED", "true")
    monkeypatch.setenv("INSERT_CACHE_TTL_HOURS", "2")
    s = Settings()
    assert s.INSERT_CACHE_ENABLED is True
    assert s.INSERT_CACHE_TTL_HOURS == 2.0  # noqa: PLR2004


def test_insert_cache_rejects_nonpositive_ttl(monkeypatch):
    """TTL <= 0 would make every entry instantly stale — fail at load, not mid-request."""
    monkeypatch.setenv("INSERT_CACHE_TTL_HOURS", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_insert_cache_rejects_zero_max_entries(monkeypatch):
    monkeypatch.setenv("INSERT_CACHE_MAX_ENTRIES", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_dataset37_refresh_timeout_default_and_validation():
    """Blocking-refresh timeout (ADR-0012): default 15s, must be > 0."""
    # Assert the field default directly so a local .env cannot affect the result.
    assert Settings.model_fields["DATASET37_REFRESH_TIMEOUT_SECONDS"].default == 15.0  # noqa: PLR2004
    # A non-positive blocking timeout would make every refresh "time out"
    # instantly and always serve stale — fail at load, not mid-request.
    with pytest.raises(ValidationError):
        Settings(DATASET37_REFRESH_TIMEOUT_SECONDS=0.0)  # type: ignore[call-arg]


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


def test_dataset42_defaults():
    s = Settings()  # type: ignore[call-arg]
    assert s.DATASET42_TTL_HOURS == 24  # noqa: PLR2004
    assert str(s.DATASET42_CACHE_DIR).endswith("dataset42")
