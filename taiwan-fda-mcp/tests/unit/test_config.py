# path: tests/unit/test_config.py
# brief: Verify Settings defaults — notably the per-user OS cache dir (uvx-safe).

from taiwan_fda_mcp.config import get_settings


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
