# path: tests/unit/test_insert_cache.py
# brief: Verify the opt-in in-memory insert cache (ADR-0011).

import asyncio
from pathlib import Path

import pytest

import taiwan_fda_mcp.sources.insert.cache as cache_mod
from taiwan_fda_mcp.exceptions import InsertParseError
from taiwan_fda_mcp.sources.insert.cache import _MB, InsertCache


@pytest.fixture
def sample_xml() -> bytes:
    return (Path(__file__).parent.parent / "fixtures" / "getdrugdoc_sample.xml").read_bytes()


def _counting_fetch(log: list[str], key: str, payload: bytes):
    """An async thunk that appends `key` to `log` each time it runs, returns payload."""

    async def fetch() -> bytes:
        log.append(key)
        return payload

    return fetch


@pytest.mark.asyncio
async def test_disabled_is_transparent_passthrough(sample_xml):
    calls: list[str] = []
    cache = InsertCache(enabled=False)
    r1 = await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    r2 = await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    assert calls == ["X", "X"]  # never cached
    assert r1.from_cache is False
    assert r2.from_cache is False
    assert len(r1.inserts) >= 1  # still parsed


@pytest.mark.asyncio
async def test_cold_miss_then_warm_hit(sample_xml):
    now = [1000.0]
    calls: list[str] = []
    cache = InsertCache(enabled=True, clock=lambda: now[0])
    r1 = await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    assert r1.from_cache is False
    assert r1.fetched_at == 1000.0  # noqa: PLR2004
    now[0] = 1000.0 + 2 * 3600  # 2 hours later, still within 6h TTL
    r2 = await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    assert calls == ["X"]  # second served from cache, no refetch
    assert r2.from_cache is True
    assert r2.fetched_at == 1000.0  # the REAL original fetch time, not now  # noqa: PLR2004


@pytest.mark.asyncio
async def test_expired_entry_refetches(sample_xml):
    now = [1000.0]
    calls: list[str] = []
    cache = InsertCache(enabled=True, ttl_hours=6.0, clock=lambda: now[0])
    await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    now[0] += 6 * 3600 + 1  # just past TTL
    r = await cache.get_or_fetch("X", _counting_fetch(calls, "X", sample_xml))
    assert calls == ["X", "X"]
    assert r.from_cache is False


@pytest.mark.asyncio
async def test_concurrent_misses_fetch_exactly_once(sample_xml):
    """Per-key herd lock: N concurrent misses for one license fetch once."""
    calls: list[str] = []

    async def slow_fetch() -> bytes:
        calls.append("X")
        await asyncio.sleep(0)  # yield so all tasks pile onto the lock
        return sample_xml

    cache = InsertCache(enabled=True)
    results = await asyncio.gather(*(cache.get_or_fetch("X", slow_fetch) for _ in range(5)))
    assert calls == ["X"]  # exactly one network fetch
    assert sum(r.from_cache for r in results) == 4  # the 4 waiters got the cached entry  # noqa: PLR2004


@pytest.mark.asyncio
async def test_distinct_keys_do_not_serialize(sample_xml):
    """Different licenses use different locks → their fetches overlap."""
    in_flight = 0
    peak = 0

    async def fetch() -> bytes:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return sample_xml

    cache = InsertCache(enabled=True)
    await asyncio.gather(cache.get_or_fetch("A", fetch), cache.get_or_fetch("B", fetch))
    assert peak == 2  # both fetched concurrently  # noqa: PLR2004


@pytest.mark.asyncio
async def test_evicts_oldest_over_entry_cap(sample_xml):
    calls: list[str] = []
    cache = InsertCache(enabled=True, max_entries=2, max_mb=1024.0)
    await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))
    await cache.get_or_fetch("B", _counting_fetch(calls, "B", sample_xml))
    await cache.get_or_fetch("C", _counting_fetch(calls, "C", sample_xml))  # over cap → evict A
    r_b = await cache.get_or_fetch("B", _counting_fetch(calls, "B", sample_xml))  # still cached
    r_a = await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))  # evicted → refetch
    assert r_b.from_cache is True
    assert r_a.from_cache is False
    assert calls == ["A", "B", "C", "A"]


@pytest.mark.asyncio
async def test_evicts_over_byte_cap(sample_xml):
    size = len(sample_xml)
    calls: list[str] = []
    # Cap holds ~1.5 sample inserts → the 2nd insert evicts the 1st on bytes alone.
    cache = InsertCache(enabled=True, max_entries=1000, max_mb=(size * 1.5) / _MB)
    await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))
    await cache.get_or_fetch("B", _counting_fetch(calls, "B", sample_xml))  # 2*size > 1.5*size → evict A
    r_a = await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))
    assert r_a.from_cache is False
    assert calls == ["A", "B", "A"]


@pytest.mark.asyncio
async def test_single_oversize_insert_is_served_but_not_cached(sample_xml):
    size = len(sample_xml)
    calls: list[str] = []
    cache = InsertCache(enabled=True, max_mb=(size * 0.5) / _MB)  # every insert exceeds the cap
    r1 = await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))
    r2 = await cache.get_or_fetch("A", _counting_fetch(calls, "A", sample_xml))
    assert r1.from_cache is False
    assert r2.from_cache is False
    assert calls == ["A", "A"]  # refetched — never stored


@pytest.mark.asyncio
async def test_empty_parse_result_is_not_cached(monkeypatch):
    """A validly-parsed-but-EMPTY result must not be cached — a transient TFDA
    200+zero-docs would otherwise serve a false 'not found' for the whole TTL.

    Monkeypatches the parser to return [] so the test does not depend on the exact
    shape of an empty GetDrugDoc envelope."""
    monkeypatch.setattr(cache_mod, "parse_get_drug_doc", lambda _raw: [])
    calls: list[str] = []

    async def fetch() -> bytes:
        calls.append("X")
        return b"<valid-but-empty/>"

    cache = InsertCache(enabled=True)
    r1 = await cache.get_or_fetch("X", fetch)
    r2 = await cache.get_or_fetch("X", fetch)
    assert r1.inserts == []
    assert r1.from_cache is False
    assert r2.from_cache is False  # not cached → fetched again
    assert calls == ["X", "X"]


@pytest.mark.asyncio
async def test_unparseable_body_is_not_cached(sample_xml):
    """A miss parses before storing, so an error/garbage body is never cached."""
    calls: list[str] = []

    async def bad_then_good() -> bytes:
        calls.append("X")
        return b"<not valid getdrugdoc>" if len(calls) == 1 else sample_xml

    cache = InsertCache(enabled=True)
    with pytest.raises(InsertParseError):
        await cache.get_or_fetch("X", bad_then_good)  # parse raises, nothing stored
    r = await cache.get_or_fetch("X", bad_then_good)  # retries → good body
    assert r.from_cache is False
    assert calls == ["X", "X"]
