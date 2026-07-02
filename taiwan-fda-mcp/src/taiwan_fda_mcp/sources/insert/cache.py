# path: src/taiwan_fda_mcp/sources/insert/cache.py
# brief: Opt-in in-memory cache of raw GetDrugDoc XML, keyed by license code (ADR-0011).

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from taiwan_fda_mcp.models import DrugInsert
from taiwan_fda_mcp.sources.insert.parser import parse_get_drug_doc

_logger = logging.getLogger(__name__)

_MB = 1024 * 1024
_STATS_EVERY = 100  # emit an INFO cache-stats rollup every N lookups (observability)


@dataclass(frozen=True)
class _Entry:
    """One cached insert: the raw GetDrugDoc XML and when it was pulled from TFDA."""

    raw: bytes
    fetched_at: float  # wall-clock epoch seconds of the real network fetch


@dataclass(frozen=True)
class InsertFetchResult:
    """Outcome of a cache-mediated insert fetch.

    `fetched_at` is when the bytes were actually pulled from TFDA — on a cache
    hit that is the ORIGINAL fetch time, not when this call ran. The caller uses
    it to report a truthful `retrieved_at` and to derive `cache_age_hours`.
    """

    inserts: list[DrugInsert]
    from_cache: bool
    fetched_at: float


class InsertCache:
    """Process-wide, opt-in cache of raw GetDrugDoc XML, keyed by license code.

    Stores raw XML bytes (re-parsed on every hit) rather than parsed models, so
    one entry serves any `fields`/`response_format` and the cache survives parser
    changes (ADR-0011 §1). Parsing also runs *before* a miss is stored, so an
    unparseable error body is never cached; nor is an empty (but valid) result,
    which would otherwise serve a false "not found" for the whole TTL.

    Off by default: `enabled=False` makes `get_or_fetch` a transparent
    pass-through, giving the caller one code path regardless of configuration.

    Single-instance only — the store and per-key locks are in-process and lost on
    restart (ADR-0010 single-worker constraint; Redis is the future scale-out).
    Safe under single-threaded asyncio: a dict read/assignment never interleaves
    with an `await`, so the cache-first fast path needs no lock.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        ttl_hours: float = 6.0,
        max_entries: int = 1000,
        max_mb: float = 128.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.enabled = enabled
        self.ttl_hours = ttl_hours
        self.max_entries = max_entries
        self.max_mb = max_mb
        self._clock = clock
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._total_bytes = 0
        self._locks: dict[str, asyncio.Lock] = {}
        self._hits = 0
        self._misses = 0
        self._lookups = 0

    @property
    def _ttl_seconds(self) -> float:
        return self.ttl_hours * 3600

    @property
    def _max_bytes(self) -> int:
        return int(self.max_mb * _MB)

    async def get_or_fetch(
        self, key: str, fetch: Callable[[], Awaitable[bytes]]
    ) -> InsertFetchResult:
        """Return parsed inserts for `key`, fetching + caching on a miss.

        `fetch` is an async thunk producing the raw GetDrugDoc XML bytes; it owns
        the egress throttle, so it runs ONLY on a miss (ADR-0011 §8).

        Raises whatever `fetch` raises (`InsertFetchError`) or the parser raises
        (`InsertParseError`). On either, nothing is stored.
        """
        if not self.enabled:
            raw = await fetch()
            return InsertFetchResult(
                parse_get_drug_doc(raw), from_cache=False, fetched_at=self._clock()
            )

        # Fast path — lock-free read (safe under single-threaded asyncio).
        entry = self._read_fresh(key)
        if entry is not None:
            self._record(key, hit=True)
            return InsertFetchResult(
                parse_get_drug_doc(entry.raw), from_cache=True, fetched_at=entry.fetched_at
            )

        # Slow path — one fetch per license under a per-key lock; waiters re-read.
        async with self._lock_for(key):
            entry = self._read_fresh(key)  # double-check: a waiter may have filled it
            if entry is not None:
                self._record(key, hit=True)
                return InsertFetchResult(
                    parse_get_drug_doc(entry.raw), from_cache=True, fetched_at=entry.fetched_at
                )

            self._record(key, hit=False)
            raw = await fetch()
            inserts = parse_get_drug_doc(raw)  # validate BEFORE storing; raise → nothing cached
            fetched_at = self._clock()
            # Do NOT cache an empty (but validly-parsed) result: TFDA can transiently
            # return 200 + zero documents for a real drug, and caching that would serve
            # a false INSERT_NOT_FOUND for the whole TTL. Serve live, store nothing.
            if inserts:
                self._store_bounded(key, raw, fetched_at)
            else:
                _logger.info("insert.cache.skip_empty", extra={"license_code": key})
            return InsertFetchResult(inserts, from_cache=False, fetched_at=fetched_at)

    def _record(self, key: str, *, hit: bool) -> None:
        """Log this lookup at DEBUG and tally it, emitting an INFO rollup every
        `_STATS_EVERY` lookups.

        Per-lookup hit/miss lines stay at DEBUG (one per request would flood an
        INFO log), but operators run at INFO by default — so the periodic rollup
        (entries, bytes, hit-rate) is what actually lets them tune TTL + caps
        (ADR-0011 §11)."""
        _logger.debug(
            "insert.cache.hit" if hit else "insert.cache.miss",
            extra={"license_code": key},
        )
        self._lookups += 1
        if hit:
            self._hits += 1
        else:
            self._misses += 1
        if self._lookups % _STATS_EVERY == 0:
            _logger.info(
                "insert.cache.stats",
                extra={
                    "lookups": self._lookups,
                    "hits": self._hits,
                    "misses": self._misses,
                    "hit_rate": round(self._hits / self._lookups, 3),
                    "entries": len(self._store),
                    "total_bytes": self._total_bytes,
                },
            )

    def _read_fresh(self, key: str) -> _Entry | None:
        """Return the entry for `key` iff present and within TTL, else None.

        Pure read — never mutates the store, so it is safe on the lock-free path.
        An expired entry is left in place; it is replaced on the next miss, or
        evicted later (oldest-first).
        """
        entry = self._store.get(key)
        if entry is None:
            return None
        if (self._clock() - entry.fetched_at) >= self._ttl_seconds:
            return None
        return entry

    def _store_bounded(self, key: str, raw: bytes, fetched_at: float) -> None:
        """Insert (or replace) an entry, then evict oldest-first to honour the caps.

        Any prior entry for `key` is dropped FIRST — including when the new body is
        oversize — so a drug that grows past the byte cap never leaves a stale
        entry occupying the budget. A single insert larger than the byte cap is then
        NOT stored (caching it would force out everything else); it is served live
        and logged.
        """
        old = self._store.pop(key, None)  # drop any prior entry first (also on oversize)
        if old is not None:
            self._total_bytes -= len(old.raw)
        if len(raw) > self._max_bytes:
            _logger.warning(
                "insert.cache.skip_oversize",
                extra={"license_code": key, "size_bytes": len(raw), "max_bytes": self._max_bytes},
            )
            return
        self._store[key] = _Entry(raw, fetched_at)  # inserted at the newest end
        self._total_bytes += len(raw)
        self._evict()

    def _evict(self) -> None:
        """Drop oldest entries until BOTH the entry cap and the byte cap hold."""
        while self._store and (
            len(self._store) > self.max_entries or self._total_bytes > self._max_bytes
        ):
            evicted_key, evicted = self._store.popitem(last=False)  # oldest first
            self._total_bytes -= len(evicted.raw)
            _logger.info(
                "insert.cache.evict",
                extra={
                    "license_code": evicted_key,
                    "entries": len(self._store),
                    "total_bytes": self._total_bytes,
                },
            )

    def _lock_for(self, key: str) -> asyncio.Lock:
        """Return the per-license lock, creating it on first use.

        The lock dict grows at most to the set of licenses fetched this process —
        bounded (by the ~26K-license universe) and each lock is tiny.
        """
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def clear(self) -> None:
        """Drop all entries + stats (test isolation; manual invalidation = restart)."""
        self._store.clear()
        self._total_bytes = 0
        self._hits = self._misses = self._lookups = 0


# Constructed at import time: asyncio.Lock() binds to a loop lazily on first await
# (Python 3.10+), so the singleton's lazily-created per-key locks are safe with no
# running loop at import. Mirrors the throttle singleton.
_default_cache = InsertCache()


def get_insert_cache() -> InsertCache:
    """Return the process-wide insert cache singleton."""
    return _default_cache
