# ADR-0011: Opt-in in-memory package-insert cache

- **Status**: Proposed
- **Date**: 2026-06-02
- **Extends**: [ADR-0009](./0009-distributed-install-data-freshness.md) (which deliberately did *not* cache inserts)
- **Related**: [ADR-0010](./0010-http-transport-hosting-model.md) (shared HTTP hosting — the main consumer; supplies the single-instance constraint)

## Context

ADR-0009 caches only the bulk **search index** (Dataset 37) and deliberately does
**not** cache package inserts: `get_package_insert` fetches `mcp.fda.gov.tw`
GetDrugDoc live on every call, because the insert is the clinically-live content.

ADR-0010's Model B (one shared HTTP service for an institution) changes the cost
of that choice: every clinician's `get_package_insert` now leaves through **one
egress IP**. Concentrated live fetching risks TFDA-side rate limiting / IP blocking
that takes the whole institution offline at once (ADR-0010 review #2). Individuals
re-querying the same drugs also waste bandwidth.

We want an **opt-in** cache that cuts repeat egress and traffic, that an individual
*or* an institution can switch on, **without** silently eroding the freshness and
citation discipline ADR-0009 protected. The hard tension: inserts carry the
strongest clinical safety signal (加框警語 / `special_warning`, ADR-0002/0007
MUST-quote), so any staleness is a safety question, not just performance.

ADR-0010 fixes the deployment as **single worker / single instance** for v1
(accepted). That lets the cache live **in process memory** — the simplest store
that is correct under one instance, and it sidesteps the on-disk concerns (atomic
writes, mtime double-duty, volume mounting) entirely.

## Decision

Add an **opt-in, off-by-default, in-memory** insert cache. Default behaviour is
unchanged (`get_package_insert` fetches live); deployments that re-query inserts
often — above all the Model B shared service — switch it on.

1. **Store: in-process memory.** A module-level dict keyed by `license_code` →
   `(raw_xml_bytes, fetched_at_epoch)`. Stores the **raw GetDrugDoc XML bytes**
   (re-parsed on hit — robust to parser changes; one entry serves every `fields` /
   `response_format`). No disk, no serialization.
2. **Short TTL.** `INSERT_CACHE_TTL_HOURS = 6.0`, configurable, validated `> 0`,
   judged against the stored `fetched_at` timestamp (not file mtime — there is no
   file).
3. **Bounded by entries AND bytes.** `INSERT_CACHE_MAX_ENTRIES` (validated `≥ 1`,
   default **1000**) plus a RAM cap `INSERT_CACHE_MAX_MB` (default **128**), since
   one insert can be MBs with embedded base64 images and this now lives in RAM.
   - **Eviction = take-the-stricter, oldest-first.** After inserting, while
     `entries > MAX_ENTRIES` **or** `total_bytes > MAX_MB`, pop the oldest
     (`OrderedDict.popitem(last=False)`) until both hold. Whichever cap binds first
     wins. Eviction is O(1) pops, no I/O.
   - **Byte accounting is exact and free** — entry size = `len(raw_xml_bytes)` (we
     store raw bytes). Maintain a running `total_bytes` counter (`+=` on insert,
     `-=` on evict); never re-sum the dict.
   - **A single insert larger than `MAX_MB` is NOT cached** — serve the freshly
     parsed result and log a warning. This keeps `MAX_MB` a hard ceiling (one giant
     insert can never blow the RAM budget); the cost is that drug refetches each time
     (rare if `MAX_MB` is sized generously).
   - **Phasing:** `MAX_ENTRIES` ships first; `MAX_MB` may follow, but the
     `total_bytes` counter is built from the start (and logged) so enabling the byte
     cap needs no data-structure change and gives operators real footprint data to
     tune against.
   - **Sizing guidance** (estimates — measure real inserts, incl. an image-bearing
     one, to tune): text-only insert ≈ 30–150 KB, image-bearing ≈ 0.5–3 MB, typical
     ≈ 150–250 KB. So 128 MB ≈ ~1000 typical entries (the two defaults align).
     512 MB-RAM container → consider `MAX_MB=64`; 2 GB+ and minimise TFDA hits →
     `256–512`; individual stdio → `32–64`.
4. **No SWR.** Unlike the search index (a single large object where a blocking
   refresh stalls a whole query window), an insert miss blocks on **one license's**
   fetch — acceptable. SWR's per-key background-task complexity is not justified.
5. **Thundering-herd guard.** A per-`license_code` `asyncio.Lock` with a
   **double-checked** memo read *inside* the lock: concurrent misses for one license
   fetch exactly once; waiters return the just-stored entry. The cache-first fast
   path is lock-free — and safe to be lock-free because a dict read/assignment is
   atomic under CPython + single-threaded asyncio (no `await` mid-update → no torn
   read).
6. **Scope: only the license-keyed `get_package_insert` path.** `check_insert_updates`
   (a date-range sweep returning many inserts, different semantics) does **not** go
   through this cache. The client refactor that separates "fetch bytes" from "parse"
   must keep the two paths distinct.
7. **Freshness honesty.** On a hit, `retrieved_at` reports the **real** fetch time
   and the response carries `from_cache: bool` + `cache_age_hours: float | None`
   (additive → snapshot regen). The cited `last_update_date` is the insert's own
   version date — **unaffected by caching** — so clinical citations stay truthful;
   `cache_age_hours` only reflects how recently we re-pulled from TFDA. The LLM must
   not conflate the two.
8. **Composition.** Cache-first → the ADR-0010 egress throttle fires only on a miss.
9. **Single-instance only; lost on restart.** The dict and locks are in-process, so
   the cache is inherently per-instance and **wiped on restart** (a cold re-warm
   burst — bounded by the egress throttle). Both are accepted under ADR-0010's
   single-instance v1 constraint.
10. **No token-saving short-circuit.** The win is upstream traffic + IP-block
    protection, not per-call LLM tokens (the response is regenerated each call).
11. **Observability.** Log `cache.hit` / `cache.miss` / `cache.evict` (structured
    JSON) so operators can tune TTL + caps. Manual invalidation = restart the
    process (memory cleared).

### Future: Redis when memory or sharing demands it

When the in-memory cache outgrows one instance's RAM, **or** Model B needs multiple
instances (HA) sharing one cache, migrate the store (and the herd lock) to **Redis**
— this is the same shared-state layer that ADR-0010 requires before horizontal
scaling. Tracked 🟡 in `TODO.md`, trigger-gated; not built speculatively. The
`license_code → (bytes, fetched_at)` shape maps directly onto Redis keys with a
native TTL, so the migration is mechanical.

### Safety trade-off (explicit)

With the cache on, an insert — **including a newly-added 加框警語** — can be up to
TTL (6 h) stale. Accepted because: the cache is **off by default**, the TTL is short,
`cache_age_hours` surfaces the age, and the cited `last_update_date` stays truthful.

**Open question / planned mitigation:** tie selective invalidation to the existing
`check_insert_updates` feed — when it reports a license changed, drop that license's
memo entry. Binds freshness to the real update signal instead of a blind TTL.
Designed when implemented.

### Rejected alternatives

- **On-disk file cache** (raw XML, one file per license) — the earlier draft. Rejected
  under the single-instance v1: it adds atomic-write (temp + `os.replace`), mtime
  double-duty (TTL vs LRU), and a writable-volume mount, all to buy cross-restart
  persistence we do not need yet. In-memory is simpler and correct for one instance;
  Redis is the scale-out path, not files.
- **Cache parsed `DrugInsert` models** — minor RAM/CPU trade; raw bytes + re-parse
  keeps robustness to parser changes and a trivial Redis-migration shape.
- **Cache the final `GetPackageInsertResponse`** keyed by (license, fields, format) —
  low hit rate, redundant storage; the upstream XML is the costly part.
- **SWR for inserts** — per-key background-task complexity unjustified for a
  single-license blocking fetch.

## Consequences

**Positive**
- Cuts repeat egress to `mcp.fda.gov.tw`, directly reducing the Model B IP-block risk.
- In-memory is the simplest correct store under single-instance: no files, no atomic
  writes, no volume, no mtime nuance, O(1) eviction.
- One shared (in-process) cache → all institution agents get consistent answers.
- Off by default → individual `uvx` users keep ADR-0009 live-fetch behaviour untouched.
- Cached content is official TFDA data (not vendor-uploaded), so the indirect-injection
  surface (invariant #3) is not widened.

**Negative / accepted trade-offs**
- Up-to-6 h staleness on cache-enabled deployments, incl. safety-critical warnings
  (see Safety trade-off).
- Cache wiped on every restart → a cold re-warm burst against TFDA (bounded by the
  throttle).
- Holds inserts in RAM → needs an entries/bytes cap; a careless cap could pressure
  memory.
- Inherently single-instance; HA / larger-than-RAM requires the Redis migration.

**Neutral**
- Adds `INSERT_CACHE_ENABLED` / `INSERT_CACHE_TTL_HOURS` / `INSERT_CACHE_MAX_ENTRIES`
  (+ optional `INSERT_CACHE_MAX_MB`) to `.env`. No cache-dir setting (in-memory).
- Adds `from_cache` + `cache_age_hours` to `GetPackageInsertResponse` (snapshot regen).

## Verification

- `INSERT_CACHE_ENABLED=false` → fetches on every call (default unchanged; assert
  fetch call count).
- `=true`: cold miss fetches + stores; a warm hit within TTL does **not** fetch and
  returns `from_cache=true` with `retrieved_at` == the real fetch time; an expired
  entry refetches and replaces.
- `cache_age_hours` is accurate on a hit; `last_update_date` identical cached vs live.
- Concurrent requests for one uncached license fetch **exactly once** (per-key lock;
  `asyncio.gather` of N calls vs a counting+sleeping fake fetch → count == 1);
  different licenses do not serialise.
- Over `MAX_ENTRIES` **or** `MAX_MB` → oldest-first evicted until both hold; memo
  never exceeds either cap; `total_bytes` tracks the dict exactly.
- A single insert larger than `MAX_MB` is served live but **not** stored (logged).
- `check_insert_updates` neither reads nor writes the insert memo.
- Cache is empty after a process restart (in-memory, by design).

## References

- Backlog: `.private/docs/TODO.md` — 🟡 env-switchable insert cache (full TDD steps),
  🟡 Redis shared-state layer (HA / memory scale-out), 🔴 TFDA insert egress throttle.
- Research: `.private/docs/sources/fastmcp-run-and-openai-agents-mcp-client.md`.
- Code to change: `taiwan-fda-mcp/src/taiwan_fda_mcp/{config.py,tools.py,
  tool_responses.py,sources/insert/client.py}`; new in-memory cache in `tools.py`
  (or a small `sources/insert/cache.py`).
- [ADR-0009](./0009-distributed-install-data-freshness.md) — why inserts were not cached
  before (this ADR reverses that *only* opt-in, preserving the default).
- [ADR-0010](./0010-http-transport-hosting-model.md) — shared-hosting profile + the
  single-instance constraint this cache depends on; Redis is its shared-state layer.
- ADR-0002 / ADR-0007 — the 加框警語 MUST-quote rule the Safety trade-off must respect.
