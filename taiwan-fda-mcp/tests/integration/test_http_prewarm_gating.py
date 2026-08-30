# path: tests/integration/test_http_prewarm_gating.py
# brief: Runtime proof that HTTP startup pre-warm gates readiness (ADR-0010 Model B).

"""Runtime verification of the pre-warm readiness-gating claim.

The unit tests in test_mcp_server.py prove the *logic* of `_prewarm_stores`
(http warms all three stores, stdio warms none, failures never raise) and that
`_lifespan` invokes it. What they cannot prove is the load-bearing architectural
claim behind the whole design: that the server accepts NO traffic until the
lifespan startup — and therefore the warm — completes.

This test proves it end-to-end against a REAL uvicorn server running FastMCP's
actual HTTP app: the real `_lifespan` runs the real `_prewarm_stores` on the
`http` branch, whose NHI warm is held open by an event (no network — we are
testing the gate, not the download). While the warm is in flight, `/health` is
unreachable; once the warm is released, `/health` answers 200.

Marked `integration` (excluded from the default `-m 'not integration'` run)
because it spins a real server and depends on startup timing, even though it
touches no network. Run explicitly:

    uv run pytest -m integration tests/integration/test_http_prewarm_gating.py
"""

import asyncio
import socket
import types

import httpx
import pytest
import uvicorn

import taiwan_fda_mcp.mcp_server as srv
from taiwan_fda_mcp.config import Settings

_STARTUP_TIMEOUT = 10.0  # generous ceiling; a hang here is a real failure, not slowness


def _free_port() -> int:
    """Grab an OS-assigned free port on localhost (tiny TOCTOU race, fine for a test)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _probe(url: str) -> int | None:
    """GET the URL; return the status code, or None if unreachable/timed out."""
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            resp = await client.get(url)
            return resp.status_code
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    ):
        return None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_http_health_gated_until_prewarm_completes(monkeypatch):
    """A real uvicorn server does not answer /health until the startup warm finishes."""
    warm_started = asyncio.Event()
    warm_release = asyncio.Event()

    async def slow_nhi_warm(_settings):
        warm_started.set()
        await warm_release.wait()  # hold the warm open until the test releases it
        return ({}, {})

    async def fast_ds37(_settings):
        return []

    async def fast_appearance(_settings):
        return {}

    async def noop_shutdown(*_a, **_k):
        return None

    # Real _lifespan + real _prewarm_stores (http branch); only the store entry
    # points and shutdown are faked, so the warm blocks deterministically offline.
    monkeypatch.setattr(srv, "get_settings", lambda: Settings(MCP_TRANSPORT="http"))  # type: ignore[call-arg]
    monkeypatch.setattr(
        srv,
        "get_nhi_store",
        lambda: types.SimpleNamespace(get_indexes=slow_nhi_warm, shutdown=noop_shutdown),
    )
    monkeypatch.setattr(
        srv,
        "get_appearance_store",
        lambda: types.SimpleNamespace(get_index=fast_appearance, shutdown=noop_shutdown),
    )
    monkeypatch.setattr(srv, "_load_or_refresh_licenses", fast_ds37)
    monkeypatch.setattr(srv, "_shutdown_refresh", noop_shutdown)

    port = _free_port()
    url = f"http://127.0.0.1:{port}/health"
    config = uvicorn.Config(
        srv.mcp.http_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    try:
        # Wait until the warm is provably in flight.
        await asyncio.wait_for(warm_started.wait(), timeout=_STARTUP_TIMEOUT)

        # Mid-warm: /health must NOT be reachable. Probe twice across a short
        # window so a lucky single miss cannot pass the test.
        assert await _probe(url) is None
        await asyncio.sleep(0.2)
        assert await _probe(url) is None
        assert server.started is False  # uvicorn has not entered its serving loop

        # Release the warm; the server should now come up and serve /health.
        warm_release.set()
        for _ in range(int(_STARTUP_TIMEOUT / 0.05)):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started is True
        assert await _probe(url) == 200  # noqa: PLR2004
    finally:
        warm_release.set()  # ensure the warm can never hang teardown
        server.should_exit = True
        await asyncio.wait_for(serve_task, timeout=_STARTUP_TIMEOUT)
