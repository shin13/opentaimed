# path: tests/unit/test_insert_throttle.py
# brief: Verify the process-wide insert egress min-interval throttle.

import asyncio
import itertools
import time

import pytest

from taiwan_fda_mcp.sources.insert.throttle import (
    InsertEgressThrottle,
    get_insert_throttle,
)


@pytest.mark.asyncio
async def test_disabled_throttle_does_not_wait():
    """min_interval <= 0 means the gate is off — acquire returns immediately."""
    throttle = InsertEgressThrottle(min_interval=0.0)
    start = time.monotonic()
    await throttle.acquire()
    await throttle.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.02  # noqa: PLR2004 — generous upper bound for "no wait"


@pytest.mark.asyncio
async def test_sequential_acquires_are_spaced():
    """First acquire is free; each subsequent one waits >= min_interval."""
    interval = 0.05
    throttle = InsertEgressThrottle(min_interval=interval)
    start = time.monotonic()
    await throttle.acquire()  # free
    await throttle.acquire()  # waits ~interval
    await throttle.acquire()  # waits ~interval
    elapsed = time.monotonic() - start
    # asyncio.sleep never returns early, so this lower bound is reliable.
    assert elapsed >= 2 * interval


@pytest.mark.asyncio
async def test_concurrent_acquires_are_serialized_and_spaced():
    """The core Model-B guarantee: concurrent callers leave spaced apart."""
    interval = 0.05
    throttle = InsertEgressThrottle(min_interval=interval)
    pass_times: list[float] = []

    async def worker() -> None:
        await throttle.acquire()
        pass_times.append(time.monotonic())

    await asyncio.gather(*(worker() for _ in range(3)))

    pass_times.sort()
    gaps = [b - a for a, b in itertools.pairwise(pass_times)]
    assert all(gap >= interval * 0.9 for gap in gaps), gaps


def test_get_insert_throttle_returns_singleton():
    assert get_insert_throttle() is get_insert_throttle()


@pytest.mark.asyncio
async def test_reset_clears_gate_state():
    """reset() lets a long-idle or test-reused throttle start fresh."""
    throttle = InsertEgressThrottle(min_interval=10.0)
    await throttle.acquire()  # arms _next_allowed 10s into the future
    throttle.reset()
    start = time.monotonic()
    await throttle.acquire()  # should NOT wait 10s
    assert time.monotonic() - start < 0.02  # noqa: PLR2004
