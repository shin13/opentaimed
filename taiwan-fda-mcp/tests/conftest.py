# path: tests/conftest.py
# brief: Shared pytest fixtures.

from pathlib import Path

import pytest

from taiwan_fda_mcp.sources.insert.cache import get_insert_cache
from taiwan_fda_mcp.sources.insert.throttle import get_insert_throttle


@pytest.fixture(autouse=True)
def _reset_insert_throttle():
    """Reset the process-wide insert egress throttle before each test.

    The singleton's min_interval and gate state would otherwise leak across
    tests (e.g. the tools test arms it to 0.7s)."""
    throttle = get_insert_throttle()
    throttle.min_interval = 0.0
    throttle.reset()


@pytest.fixture(autouse=True)
def _reset_insert_cache():
    """Reset the process-wide insert cache before each test (state would leak)."""
    cache = get_insert_cache()
    cache.enabled = False
    cache.ttl_hours = 6.0
    cache.max_entries = 1000
    cache.max_mb = 128.0
    cache.clear()


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to tests/fixtures/ directory."""
    return Path(__file__).parent / "fixtures"
