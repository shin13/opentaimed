# path: src/taiwan_fda_mcp/sources/nhi/store.py
# brief: Process-wide NHI item indexes with a two-tier (probe / payload) refresh.

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime

from taiwan_fda_mcp.config import Settings
from taiwan_fda_mcp.exceptions import DatasetFetchError
from taiwan_fda_mcp.models import NhiCacheMeta, NhiDrugItem
from taiwan_fda_mcp.sources.nhi.client import fetch_drug_items, probe_metadata
from taiwan_fda_mcp.sources.nhi.dataset import (
    cache_mtime,
    load_from_cache,
    read_meta,
    write_meta,
    write_to_cache,
)

_logger = logging.getLogger(__name__)


class NhiItemStore:
    """Two indexes over one parsed row list, refreshed in two tiers.

    Tier 1 is a 2 KB metadata probe (~0.6 s) and MAY block a stale query.
    Tier 2 is the 92 MB payload and NEVER blocks a query.

    Version identity is the payload sha256, not an upstream timestamp: the same
    metadata response has been observed carrying a `modified` 13 days later than
    its own `resourceModified`, so neither is trusted to track the payload. A
    lying timestamp therefore costs a download, never a wrong answer.
    """

    def __init__(self) -> None:
        self._rows: list[NhiDrugItem] | None = None
        self._by_code: dict[str, NhiDrugItem] = {}
        self._by_licence: dict[str, list[NhiDrugItem]] = {}
        self._meta: NhiCacheMeta | None = None
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None

    async def get_indexes(
        self, settings: Settings
    ) -> tuple[dict[str, NhiDrugItem], dict[str, list[NhiDrugItem]]]:
        """Return (nhi_code → row, license_no → rows), refreshing as policy allows."""
        if self._rows is not None and not self._is_stale(settings):
            return self._by_code, self._by_licence  # fast path

        async with self._lock:
            if self._rows is None:
                await self._cold_load(settings)  # disk, else one blocking download
            # Fall through rather than `elif`: a cold load from a STALE disk
            # cache must still get the freshness policy applied, exactly as
            # Dataset 37's loader does (tools.py `_load_or_refresh_licenses`).
            # With `elif`, the first query after a restart would serve a
            # month-old snapshot and never probe.
            if self._is_stale(settings):
                await self._probe_and_maybe_schedule(settings)
        return self._by_code, self._by_licence

    async def _cold_load(self, settings: Settings) -> None:
        """Populate from disk if present, else block on one download (may raise)."""
        disk = load_from_cache(settings.NHI_CACHE_DIR)
        if disk is not None:
            self._install(disk, loaded_at=cache_mtime(settings.NHI_CACHE_DIR))
            self._meta = read_meta(settings.NHI_CACHE_DIR)
            return

        # Probe BEFORE downloading so the sidecar records the timestamps that
        # were current when the download started (spec 6.1). Without this the
        # sidecar would hold empty strings, every fresh install's first probe
        # would read "changed", and each one would re-download 92 MB after 24 h
        # even though nothing upstream moved.
        try:
            probed = await probe_metadata(
                settings.NHI_BASE_URL,
                timeout=settings.NHI_PROBE_TIMEOUT_SECONDS,
                rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            )
            modified, resource_modified = probed.modified, probed.resource_modified
        except DatasetFetchError:
            # A probe failure must not fail a cold start — the payload is what
            # the caller needs. Empty timestamps cost one extra download on the
            # next cycle, which beats refusing to serve at all.
            _logger.warning("nhi.cold_load.probe_failed")
            modified, resource_modified = "", ""

        rows, digest, size = await fetch_drug_items(
            settings.NHI_BASE_URL,
            rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
        )  # raises DatasetFetchError — nothing to serve on a true first run
        self._persist(
            settings, rows, digest, size, modified=modified, resource_modified=resource_modified
        )

    async def _probe_and_maybe_schedule(self, settings: Settings) -> None:
        """Blocking 2 KB probe. Re-stamp if unchanged, else schedule a download."""
        try:
            meta = await probe_metadata(
                settings.NHI_BASE_URL,
                timeout=settings.NHI_PROBE_TIMEOUT_SECONDS,
                rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            )
        except DatasetFetchError:
            _logger.warning("nhi.probe.failed")
            self._trigger_background_refresh(settings)
            return  # keep the stale memo; is_stale stays true

        stored = self._meta
        unchanged = (
            stored is not None
            and stored.modified == meta.modified
            and stored.resource_modified == meta.resource_modified
        )
        if unchanged:
            self._loaded_at = time.time()  # honestly fresh — upstream said nothing moved
            _logger.info("nhi.probe.unchanged")
            return

        _logger.info(
            "nhi.probe.changed",
            extra={"modified": meta.modified, "resource_modified": meta.resource_modified},
        )
        self._trigger_background_refresh(settings)

    def _trigger_background_refresh(self, settings: Settings) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return  # single in-flight guard
        self._refresh_task = asyncio.create_task(self._background_reload(settings))

    async def _background_reload(self, settings: Settings) -> None:
        """Download the payload off the query path; swap in only on a new hash.

        The network work runs OUTSIDE the lock and only the state swap is
        serialised. This deliberately differs from AppearanceStore, which holds
        its lock across `fetch_dataset42`: that payload is small, whereas this
        one is 92 MB and takes over two minutes, so holding the lock across the
        download would block every query for the whole download and destroy the
        one guarantee this policy exists to provide.
        """
        try:
            meta = await probe_metadata(
                settings.NHI_BASE_URL,
                timeout=settings.NHI_PROBE_TIMEOUT_SECONDS,
                rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            )
            rows, digest, size = await fetch_drug_items(
                settings.NHI_BASE_URL,
                rate_limit_interval=settings.FDA_RATE_LIMIT_INTERVAL_SECONDS,
            )
        except DatasetFetchError:
            _logger.warning("nhi.background_reload.failed")
            return  # keep the last-good snapshot

        async with self._lock:  # serialise the swap against a foreground probe
            stored = self._meta
            if stored is not None and stored.payload_sha256 == digest:
                # The payload did not actually change — only a timestamp moved.
                # Record the new timestamps so the next cycle does not
                # re-download, and leave the rows and the indexes untouched.
                self._meta = stored.model_copy(
                    update={
                        "modified": meta.modified,
                        "resource_modified": meta.resource_modified,
                    }
                )
                write_meta(self._meta, settings.NHI_CACHE_DIR)
                self._loaded_at = time.time()
                _logger.info("nhi.background_reload.unchanged_payload")
                return

            self._persist(
                settings,
                rows,
                digest,
                size,
                modified=meta.modified,
                resource_modified=meta.resource_modified,
            )
        _logger.info("nhi.background_reload.swapped", extra={"current_rows": len(rows)})

    def _persist(
        self,
        settings: Settings,
        rows: list[NhiDrugItem],
        digest: str,
        size: int,
        *,
        modified: str,
        resource_modified: str,
    ) -> None:
        write_to_cache(rows, settings.NHI_CACHE_DIR)
        self._meta = NhiCacheMeta(
            payload_sha256=digest,
            content_length=size,
            row_count=len(rows),
            modified=modified,
            resource_modified=resource_modified,
            downloaded_at=datetime.now(UTC).isoformat(),
        )
        write_meta(self._meta, settings.NHI_CACHE_DIR)
        self._install(rows, loaded_at=time.time())

    def _install(self, rows: list[NhiDrugItem], *, loaded_at: float | None) -> None:
        by_licence: dict[str, list[NhiDrugItem]] = defaultdict(list)
        for row in rows:
            if row.license_no:
                by_licence[row.license_no].append(row)
        self._rows = rows
        self._by_code = {r.nhi_code: r for r in rows}
        self._by_licence = dict(by_licence)
        self._loaded_at = loaded_at

    def _is_stale(self, settings: Settings) -> bool:
        if self._loaded_at is None:
            return True
        return (time.time() - self._loaded_at) >= settings.NHI_TTL_HOURS * 3600

    def freshness(self, settings: Settings) -> tuple[str | None, float | None, bool]:
        """(retrieved_at ISO, age_hours, is_stale) for the currently-served memo."""
        if self._loaded_at is None:
            return None, None, False
        age_hours = (time.time() - self._loaded_at) / 3600
        retrieved_at = datetime.fromtimestamp(self._loaded_at, UTC).isoformat()
        return retrieved_at, age_hours, age_hours >= settings.NHI_TTL_HOURS

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
        self._rows = None
        self._by_code = {}
        self._by_licence = {}
        self._meta = None
        self._loaded_at = None
        self._refresh_task = None


_default_store = NhiItemStore()


def get_nhi_store() -> NhiItemStore:
    """Return the process-wide NHI item store singleton."""
    return _default_store
