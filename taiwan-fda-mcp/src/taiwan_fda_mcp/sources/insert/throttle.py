# path: src/taiwan_fda_mcp/sources/insert/throttle.py
# brief: Process-wide minimum-interval throttle for GetDrugDoc egress.

import asyncio
import logging
import time

_logger = logging.getLogger(__name__)


class InsertEgressThrottle:
    """Bound the outbound GetDrugDoc request rate, process-wide.

    A shared HTTP deployment (ADR-0010 Model B) concentrates every clinician's
    insert fetch onto one egress IP. Without a shared gate, a burst of queries
    becomes a burst of requests to a fragile government endpoint, risking a
    rate-limit block that takes down lookup for the whole institution.

    This gate serializes the *acquisition of a send slot* and spaces consecutive
    slots at least ``min_interval`` seconds apart using a monotonic clock. It
    bounds the send rate, not the number of in-flight requests — responses may
    still overlap. ``min_interval <= 0`` disables the gate entirely.

    Single-threaded asyncio makes the plain attribute reads/writes here atomic;
    the lock serializes the wait-and-advance so concurrent callers queue fairly.
    """

    def __init__(self, min_interval: float = 0.0) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0  # monotonic timestamp of the next free slot

    async def acquire(self) -> None:
        """Block until the caller is allowed to send the next request."""
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                _logger.debug("insert.throttle.wait", extra={"wait_seconds": wait})
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval

    def reset(self) -> None:
        """Clear the gate so the next acquire is immediate.

        Call only when no ``acquire()`` is in flight (e.g. between tests, or
        after a long idle). This writes ``_next_allowed`` without the lock; a
        reset racing a parked ``acquire()`` would be silently clobbered when
        that acquire wakes and re-advances the gate. Kept synchronous so it can
        be called from a synchronous test-isolation fixture.
        """
        self._next_allowed = 0.0


# Constructed at import time: asyncio.Lock() binds to a loop lazily on first
# await (Python 3.10+), so creating the singleton with no running loop is safe.
_default_throttle = InsertEgressThrottle()


def get_insert_throttle() -> InsertEgressThrottle:
    """Return the process-wide insert egress throttle singleton."""
    return _default_throttle
