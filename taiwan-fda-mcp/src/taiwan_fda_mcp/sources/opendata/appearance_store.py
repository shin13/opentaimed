# path: src/taiwan_fda_mcp/sources/opendata/appearance_store.py
# brief: In-memory Dataset 42 (drug appearance) index with non-blocking refresh.

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.models import DrugAppearance
from taiwan_fda_mcp.sources.opendata.client import fetch_dataset42
from taiwan_fda_mcp.sources.opendata.dataset42 import (
    cache_mtime,
    load_from_cache,
    write_to_cache,
)

_logger = logging.getLogger(__name__)


class AppearanceStore:
    """Process-wide in-memory Dataset 42 index, keyed by license_no.

    Non-blocking refresh (ADR-0013): appearance data is not safety-time-critical,
    so a query past the TTL is served from the last snapshot while a single
    background reload runs. Only a cold start with no snapshot on disk blocks
    (once) and raises on failure.
    """

    def __init__(self) -> None:
        self._index: dict[str, DrugAppearance] | None = None
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None

    async def get_index(self, settings: Settings) -> dict[str, DrugAppearance]:
        """Return the license_no → DrugAppearance index, refreshing as policy allows."""
        idx = self._index
        if idx is not None and not self._is_stale(settings):
            return idx  # fast path — fresh memo, no lock, no network
        if idx is not None:
            self._trigger_background_refresh(settings)  # stale → serve now, reload async
            return idx
        async with self._lock:
            if self._index is not None:
                return self._index  # a concurrent caller loaded it
            await self._cold_load(settings)  # disk, else block-download once (may raise)
            assert self._index is not None
            return self._index

    async def _cold_load(self, settings: Settings) -> None:
        disk = load_from_cache(settings.DATASET42_CACHE_DIR)
        if disk is not None:
            self._index = {r.license_no: r for r in disk}
            self._loaded_at = cache_mtime(settings.DATASET42_CACHE_DIR)
            return
        rows = await fetch_dataset42(
            settings.FDA_OPENDATA_BASE_URL,
            rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
        )  # raises DatasetFetchError — nothing to serve on a true first run
        write_to_cache(rows, settings.DATASET42_CACHE_DIR)
        self._index = {r.license_no: r for r in rows}
        self._loaded_at = time.time()

    def _is_stale(self, settings: Settings) -> bool:
        if self._loaded_at is None:
            return True
        return (time.time() - self._loaded_at) >= settings.DATASET42_TTL_HOURS * 3600

    def _trigger_background_refresh(self, settings: Settings) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return  # single in-flight guard
        self._refresh_task = asyncio.create_task(self._background_reload(settings))

    async def _background_reload(self, settings: Settings) -> None:
        async with self._lock:
            if not self._is_stale(settings):
                return
            try:
                rows = await fetch_dataset42(
                    settings.FDA_OPENDATA_BASE_URL,
                    rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
                )
            except DatasetFetchError:
                _logger.warning("dataset42.background_reload.failed")
                return  # keep the stale memo
            write_to_cache(rows, settings.DATASET42_CACHE_DIR)
            self._index = {r.license_no: r for r in rows}
            self._loaded_at = time.time()

    def freshness(self, settings: Settings) -> tuple[str | None, float | None, bool]:
        """(retrieved_at ISO, age_hours, is_stale) for the currently-served memo."""
        if self._loaded_at is None:
            return None, None, False
        age_hours = (time.time() - self._loaded_at) / 3600
        retrieved_at = datetime.fromtimestamp(self._loaded_at, UTC).isoformat()
        return retrieved_at, age_hours, age_hours >= settings.DATASET42_TTL_HOURS

    async def shutdown(self) -> None:
        """Cancel any in-flight background reload (graceful SIGTERM). Idempotent."""
        task = self._refresh_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._refresh_task = None

    def reset(self) -> None:
        """Clear memo state (test isolation)."""
        self._index = None
        self._loaded_at = None
        self._refresh_task = None


_default_store = AppearanceStore()


def get_appearance_store() -> AppearanceStore:
    """Return the process-wide appearance index singleton."""
    return _default_store
