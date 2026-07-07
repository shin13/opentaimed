# ADR-0012: Over-TTL blocking refresh for the Dataset 37 search index

- **Status**: Accepted
- **Date**: 2026-07-07
- **Supersedes**: [ADR-0009](./0009-distributed-install-data-freshness.md) (stale-while-revalidate)
- **Extends**: [ADR-0010](./0010-http-transport-hosting-model.md) (shared HTTP service — the concurrency guard matters there)

## Context

[ADR-0009](./0009-distributed-install-data-freshness.md) chose
**stale-while-revalidate (SWR)** for the Dataset 37 search index: a query past
the TTL is answered **immediately from the stale copy** while a background task
re-downloads. Its stated reason for rejecting the simpler "blocking-lazy"
approach was that a mid-conversation download stall "forces one query per TTL
window to wait ~seconds for the 4.4 MB download," and "in an agentic context a
mid-conversation stall is more jarring than in a human search box."

**That premise was never measured. It has now been measured (2026-07-07, live
against `data.fda.gov.tw`, 3 runs):**

| Segment | min | avg | max |
|---|---|---|---|
| HTTP download of the ZIP (what a timeout bounds) | 0.33 s | **0.58 s** | 0.83 s |
| Full `fetch_dataset37` (download + unzip + parse 26,034 rows) | 0.55 s | **0.59 s** | 0.63 s |

The ZIP is **4.46 MB**; unzip + parse of 26 K rows is **~0.01 s** (within noise).
The "stall" SWR was built to avoid is, in practice, **sub-second**.

This reframes the trade-off. The clinically relevant risk is not latency — it is
**serving a stale license snapshot when a fresh one is ~0.6 s away.** SWR's
default is "answer now, possibly stale, refresh for *next* time"; for a drug
reference tool whose core principle is *the system is a parser of current
official data, never an author*, the right default is **"this call, the freshest
data we can get."** The unmeasured latency fear no longer justifies serving
known-stale data by default.

Two upstream facts from ADR-0009 still hold and shape the design:
- `data.fda.gov.tw` sends **no `Last-Modified` / `ETag`** — every refresh is a
  full re-download; no cheap 304 revalidation.
- Upstream refreshes **~daily**, so a 24 h TTL floor remains right.

## Decision

We replace SWR with **over-TTL blocking refresh**, keeping SWR's machinery only
as the failure-fallback path.

On every call, `_load_or_refresh_licenses` (in `tools.py`) does:

1. **Fresh memo (age < `DATASET37_TTL_HOURS`)** → serve immediately. No lock, no
   fetch. (Unchanged; the common case.)
2. **Stale memo (age ≥ TTL)** → acquire a process-wide **single-flight lock**,
   re-check freshness (a concurrent caller may have just refreshed), then attempt
   **one blocking refresh** bounded by a new
   **`DATASET37_REFRESH_TIMEOUT_SECONDS` (default 15 s)**:
   - **Success** → atomically swap the memo, serve **fresh** (`is_stale=False`).
   - **Timeout / failure** → serve the **last-good snapshot** with
     `is_stale=True`, and schedule a **background retry** (single in-flight,
     up to 3 attempts with backoff) so the *next* call is fresh.
3. **Cold start** (no memo yet):
   - **Disk cache present** → load it into the memo, then apply the freshness
     policy above (a stale disk cache therefore triggers a blocking refresh).
   - **No disk cache (true first run)** → block on one refresh; **failure raises**
     (`DATASET_FETCH_FAILED`) — there is nothing to serve. This failure semantic
     is deliberately distinct from the stale-memo path (availability vs. honesty).

**SWR is demoted, not deleted.** The `_trigger_background_refresh` /
`_REFRESH_TASK` / `shutdown()` machinery from ADR-0009 is reused verbatim as the
**failure-fallback**: it now fires from the blocking-refresh failure branch
instead of the happy path. Its single-in-flight guard is exactly what caps the
"retry up to 3 times" so concurrent failed calls never stack multiple background
refreshes. `shutdown()` and its `mcp_server.py` lifespan hook are unchanged.

**Concurrency.** A lazily-created module-level `asyncio.Lock` (mirroring the
insert-cache / throttle singletons, which avoid binding a running loop at import)
serialises refreshes. Concurrent stale callers therefore trigger **one** download;
the others wait on the lock and, on re-check, serve the freshly-loaded data
without downloading again. Every `_blocking_refresh` call happens under this lock
(foreground and background), so memo writes never race.

**`is_stale` semantics tighten — no new field.** Under SWR, `is_stale=True` was a
routine state ("stale, background refresh pending"). Under blocking refresh,
every stale serve is preceded by an inline refresh attempt, so
**`is_stale=True` now means exactly "past TTL AND a live refresh could not
complete; served from the last-good snapshot (`dataset_retrieved_at`)."** The
existing `dataset_retrieved_at` / `dataset_age_hours` / `is_stale` fields already
express this; a separate `refresh_failed` flag would be redundant and is
**rejected** to keep the response schema stable. Only the `is_stale` field
*description* changes (both `SearchDrugsResponse` and `SearchByIngredientResponse`).

## Consequences

**Positive**
- The default answer is now the **freshest data obtainable**, matching the
  project's parser-not-author / citation-discipline principle.
- Measured normal-case cost of the new blocking refresh is **<1 s**, incurred at
  most once per TTL window — imperceptible in an agent turn.
- A stale answer now carries a **precise** meaning (`is_stale=True` ⟺ live refresh
  failed), a stronger honesty signal than SWR's ambiguous "stale for some reason."
- Thundering herd on the shared HTTP service (ADR-0010) is bounded to one
  download by the single-flight lock.
- Availability is preserved: upstream outage → last-good snapshot + labelled +
  background retry, never a hard failure (except true first-run-with-no-cache).

**Negative / accepted trade-offs**
- One query per TTL window now blocks ~0.6 s (measured) instead of returning
  instantly. Accepted: sub-second, and the correctness/honesty win dominates.
- **Worst case**, on a network that accepts the connection but stalls, a call
  blocks up to `DATASET37_REFRESH_TIMEOUT_SECONDS` (15 s) before falling back to
  stale. A foreground caller that arrives while a background retry holds the lock
  can likewise wait up to 15 s — the same bound, no new risk.
- The true-first-run blocking timeout tightens from `fetch_dataset37`'s 60 s
  default to 15 s. Accepted (normal first run is <1 s); operators on a slow /
  proxied link (e.g. 院內 network behind a WAF) can raise
  `DATASET37_REFRESH_TIMEOUT_SECONDS`.
- Still a full re-download per refresh (no conditional GET) — unchanged from 0009.

**Neutral**
- `DATASET37_TTL_HOURS` (24) and `DATASET37_CACHE_DIR` stay configurable.
- Response schema is unchanged in shape; only the `is_stale` description string
  moves (a snapshot regen, not a contract break).

## Verification

- A server running past the TTL, on the next call, **blocks briefly and serves
  fresh data** (`is_stale=False`, `dataset_age_hours≈0`) — not stale-then-refresh.
- With `fetch_dataset37` failing and a prior cache present, the call serves the
  stale snapshot with `is_stale=True` and schedules exactly one background retry.
- Two concurrent stale calls trigger **exactly one** download (lock + re-check).
- True first run with no disk cache and a failing network **raises**
  `DATASET_FETCH_FAILED` (does not serve empty).
- `DATASET37_REFRESH_TIMEOUT_SECONDS` bounds the blocking wait.
- Revisit if: TFDA adds conditional-GET headers (cheap revalidation would change
  the calculus again), a push/notify feed appears, or measured download time
  rises by an order of magnitude (re-evaluate the blocking default vs. SWR).

## References

- Live timing measurement: this session (2026-07-07), 3 runs against
  `https://data.fda.gov.tw/data/opendata/export/37/json`.
- Implementation plan: `.private/docs/plans/2026-07-07-dataset37-blocking-refresh.md`.
- Code: `taiwan-fda-mcp/src/taiwan_fda_mcp/tools.py`
  (`_load_or_refresh_licenses`, `_ensure_loaded`, `_blocking_refresh`,
  `_refresh_into_memo`, `_trigger_background_refresh`, `shutdown`),
  `config.py` (`DATASET37_TTL_HOURS`, new `DATASET37_REFRESH_TIMEOUT_SECONDS`),
  `tool_responses.py` (`is_stale` description on both search responses).
- [ADR-0009](./0009-distributed-install-data-freshness.md) — the superseded SWR decision.
- [ADR-0010](./0010-http-transport-hosting-model.md) — shared HTTP service where the concurrency guard matters.
