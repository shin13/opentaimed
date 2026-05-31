# ADR-0009: Data-freshness strategy for distributed installs

- **Status**: Accepted
- **Date**: 2026-05-31
- **Extends**: [ADR-0001](./0001-tfda-dual-api-strategy.md) (dual-API), [ADR-0003](./0003-search-via-dataset37-not-lmspiq.md) (search via opendata), [ADR-0008](./0008-multi-field-search-flat-filters.md) (multi-field search)

## Context

The primary distribution profile is **self-hosted by each end user**: a
developer installs the package (git clone, or `uvx` once published) and points
their agent (Claude Code, Codex, …) at the stdio MCP server. In that profile
there is no central server, no shared cache, and **no way to require the user to
set up a cron / scheduler** — freshness must be self-contained and automatic.

(A second, future profile — a shared HTTP service hosted by an institution,
Phase 3 / future ADR-0010 — has one process and one cache, where a scheduled
background refresh becomes natural. The mechanisms below remain correct there as
the baseline.)

Two facts (live-verified 2026-05-31, see investigation doc) fix the design space:

1. **No real-time search API exists** — the website search is CAPTCHA-gated
   (ADR-0003, ADR-0008); the bulk search index must be cached from opendata.
2. **`data.fda.gov.tw` exports send no `Last-Modified` / `ETag` / `Cache-Control`**
   — cheap 304 revalidation is impossible; every refresh is a full re-download
   (~4.4 MB for Dataset 37). Upstream itself refreshes ~daily.

Scope-narrowing fact: the **clinically important content is already live** —
`get_package_insert` fetches GetDrugDoc per call. Only the bulk search index
(Dataset 37, later 41) is cached. So cache staleness affects "is a
newly-licensed drug findable yet," **not** "is this drug's info current."

Current bug: the on-disk 24 h TTL is consulted **only on the first call per
process**; the process-level memo (`_LICENSES_CACHE`) then short-circuits
forever. A long-running stdio server never refreshes until restart — the TTL is
effectively dead.

## Decision

Each install keeps its bulk data fresh **with zero user setup**, optimised so a
query never blocks on a refresh after the first load:

1. **Per-user OS cache dir** (`platformdirs.user_cache_dir("taiwan-fda-mcp")`),
   **not** `.cache/` inside the package tree — survives `git pull`, read-only /
   ephemeral `uvx` installs, and multiple concurrent agents. (Hard prerequisite
   for the `uvx` distribution in ADR-0008/Phase 2: an ephemeral install cannot
   cache inside its own package directory.)
2. **TTL-aware memo** — the memo stores its load timestamp and re-checks
   `age > TTL` on every call instead of short-circuiting forever. Fixes the
   dead-TTL bug; a correctness fix, not an optimisation.
3. **Stale-while-revalidate (SWR)** — when the memo is stale, the call **returns
   the stale data immediately** and, if no refresh is already in flight, spawns a
   **single background task** that re-downloads, atomically swaps the memo, and
   updates the timestamp. A query therefore never waits for a download except on
   the one cold start below. A background fetch failure keeps the stale memo
   (logged, `is_stale` stays true) and is retried on the next call — so a
   transient FDA outage never breaks or blocks a query.
   - **Cold start**: if neither memo nor disk cache exists (truly first run), the
     call blocks once on the download. If a *stale* disk cache exists (e.g. from a
     prior run), it is served immediately and refreshed in the background — no
     block.
   - **Concurrency**: a single in-flight-refresh guard prevents duplicate
     concurrent downloads.
4. **No bundled seed snapshot** — deployment targets are never offline, so the
   one-time cold-start download (a few seconds, first run only) is acceptable.
   Avoids repo bloat and a snapshot that goes stale in git.
5. **Explicit freshness in the response** — `SearchDrugsResponse` carries
   `dataset_retrieved_at` / `dataset_age_hours` / `is_stale`. `is_stale` is true
   while serving data older than the TTL (the brief revalidation window, or while
   a refresh keeps failing), so the LLM can state the data's age to the user
   (consistent with the project's citation discipline). Every refresh is logged.
6. **Good-citizen throttling** — the refresh fetch is rate-limited
   (`FDA_RATE_LIMIT_INTERVAL_SECONDS`); it is the only place that actively hits a
   `.gov` endpoint. `DATASET37_TTL_HOURS` stays configurable with a **recommended
   24 h floor** — below it wastes bandwidth for no freshness gain, since upstream
   refreshes only daily and no conditional GET is available.

### Rejected alternative

**Blocking-lazy** (the stale call itself downloads and waits) was considered and
rejected: it is simpler, but it forces one query per TTL window to wait ~seconds
for the 4.4 MB download. In an agentic context a mid-conversation stall is more
jarring than in a human search box, and SWR removes that recurring wait for a
small, contained complexity cost (one background task + an in-flight guard).

### Separate axis — code freshness

A clone with stale **code** can break if TFDA changes Dataset 37's schema →
defensive parsing (missing/renamed columns degrade, don't crash) + a startup
version log nudging `git pull`. Eventual `uvx` / PyPI distribution makes code
updates smoother than `git clone`; pursued in Phase 2, not decided here.

## Consequences

**Positive**
- After the first load, **queries never block on a dataset refresh** — the stale
  copy is served instantly while a background task updates it.
- The dead-TTL bug is fixed for long-running stdio servers.
- A transient fetch failure degrades to stale (flagged) rather than erroring or
  blocking; users always know the index's age via the freshness fields.
- Aggregate load on `data.fda.gov.tw` is bounded by the 24 h recommended floor +
  per-client rate limit + the single-in-flight guard.
- Cache in a per-user dir makes the package stateless — required for `uvx`.

**Negative / accepted trade-offs**
- SWR adds an `asyncio` background task + an in-flight-refresh guard + atomic memo
  swap — more moving parts than blocking-lazy (accepted for the recurring-wait UX
  win).
- During the brief revalidation window a query may get data up to the refresh
  duration stale (seconds–minutes); acceptable because it is only the search
  index, and `is_stale` flags it.
- No conditional GET → every refresh re-downloads the full export.
- No seed → the very first run with no network cannot answer; accepted because
  deployment targets are never offline.
- Per-user cache dir means the cache is not co-located with the package
  (mitigated by logging the resolved cache path at startup).

**Neutral**
- `DATASET37_TTL_HOURS` and `DATASET37_CACHE_DIR` stay configurable via `.env`.
- Adds freshness fields to the search response (additive, ADR-0006 flat shape).

## Verification

- A server running > TTL hours serves data no older than ~TTL + one refresh
  cycle without a restart, and the serving query does **not** block.
- `search_drugs` response includes `dataset_retrieved_at` / `dataset_age_hours` /
  `is_stale`.
- The cache lands under the per-user OS cache dir, not the package tree.
- With `fetch_dataset37` failing and a prior cache present, `search_drugs` still
  answers immediately with `is_stale=true` rather than raising; only a true
  first-run-with-no-cache blocks (then errors only if the network is down).
- Two stale calls in quick succession spawn at most one background refresh.
- Revisit if: TFDA adds conditional-GET headers, a push/notify feed appears, or
  the shared HTTP-service profile (ADR-0010) lands (swap SWR's per-call trigger
  for a scheduled refresh).

## References

- `.private/docs/sources/website-search-surface-and-dataset-mapping.md` (Part 2: no conditional-GET headers; Part 6: full strategy).
- Implementation plan: `docs/superpowers/plans/2026-05-31-pre-launch-distribution-and-hosting.md` (Phase 0).
- Code: `taiwan-fda-mcp/src/taiwan_fda_mcp/tools.py` (`_load_or_refresh_licenses` — the dead-TTL short-circuit), `config.py` (`DATASET37_CACHE_DIR`, `DATASET37_TTL_HOURS`, `FDA_RATE_LIMIT_INTERVAL_SECONDS`), `sources/opendata/{client.py,dataset37.py}`.
- [ADR-0008](./0008-multi-field-search-flat-filters.md) — the search expansion that rides on this cache.
