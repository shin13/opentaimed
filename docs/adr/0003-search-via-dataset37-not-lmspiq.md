# ADR-0003: `search_drugs` is backed by opendata Dataset 37, not lmspiq scraping

- **Status**: Accepted
- **Date**: 2026-05-25
- **Related**: ADR-0001 (TFDA dual-API strategy)

## Context

ADR-0001 established that OpenTaiMed uses two TFDA endpoints in tandem.
This ADR records the narrower decision *within* the metadata side of that
split: how should free-text drug search be implemented?

Three options were evaluated:

**(A) `data.fda.gov.tw` Dataset 37 — 「西藥、醫療器材、化粧品許可證資料 — 西藥」**
- ~26,000 active license rows.
- JSON ZIP download, no authentication.
- Refreshes daily.
- Stable column shape (verified across multiple monthly snapshots).
- Covers only `未註銷` (currently-valid) licenses.

**(B) `lmspiq.fda.gov.tw` — the human-facing search portal**
- Single-page application; results rendered by client-side JS against an
  internal XHR API.
- Two known endpoint URLs returned 404 from the public internet during
  May 2026 testing — strongly suggests access is gated to TSGH 院內網路
  or a VPN.
- Would require either a headless-browser stack or reverse-engineering
  the XHR endpoints (which could change at any time without notice).

**(C) `mcp.fda.gov.tw` GetDrugDoc with a date sweep**
- Iterate `check_insert_updates` over a multi-year window to enumerate
  all licenses that have ever published an insert.
- Pathologically slow (10-day max window per call → hundreds of calls)
  and returns nothing for licenses without published inserts.
- Has no name / ingredient field — would still need to be joined against
  another source for the actual search experience.

## Decision

`search_drugs` is backed by Dataset 37 exclusively for v1.

The full data flow:

1. On first use (or daily), `sources/opendata/` downloads the Dataset 37
   ZIP, extracts the JSON, and caches it locally.
2. `search_drugs(query, search_by, limit)` performs substring matching
   across the cached rows. `search_by="any"` searches name (zh + en) +
   ingredient + license number.
3. Results are sorted by license-prefix authority (import / 原廠 first)
   then `name_zh`, so the most canonical match for a generic-heavy
   ingredient surfaces at index 0.

Lmspiq and GetDrugDoc-sweep are **not** consulted by `search_drugs`.

## Consequences

**Positive**
- Sub-millisecond search latency once the cache is warm.
- Stable contract — Dataset 37 schema has been consistent over the
  observable history of the opendata portal.
- Offline-capable for development and CI.
- No scraping → no anti-bot tripwires, no fragile selectors.

**Negative / accepted trade-offs**
- **De-registered drugs are invisible.** A user looking up a discontinued
  drug (for medication history reconciliation, literature review, legacy
  prescription review) will get zero results from `search_drugs`. The
  wrapper says "查無此藥 on TFDA" — which is honest but incomplete.
  Logged as a "大議題" in TODO.md; will revisit when a real user reports
  this as blocking.
- **Up to 24-hour staleness** for newly-registered drugs. Acceptable
  given that a new license typically does not publish an insert on day 0;
  by the time a clinician would query it, the cache will have refreshed.
- **Duplicate-looking rows** — one license with multiple registered
  manufacturers appears as multiple rows with identical `license_no`.
  This is real upstream data shape (not a bug) and is surfaced verbatim;
  collapsing rows would lose the manufacturer information.

**Neutral**
- ATC code, appearance text, and full ingredient breakdowns live in
  sibling datasets (41 / 42 / 43). `search_drugs` does not currently
  join these — they are available for future tools to read.

## Verification

- `tests/unit/test_search.py` exercises substring / `search_by` /
  sort-authority behaviour against a fixture sliced from Dataset 37.
- Cache integrity: the loader fails fast if the JSON schema diverges
  from the expected column set.
- Real-API check (manual until smoke-test cron lands):
  `search_drugs("脈優")` returns a 脈優錠 row with
  `license_no="衛署藥輸字第021571號"` and `ingredient="AMLODIPINE BESYLATE"`.

Revisit when: (a) a user reports a real need for de-registered-drug
lookup, or (b) lmspiq becomes accessible without VPN (then a hybrid
"Dataset 37 first, lmspiq fallback" is reconsiderable), or (c) Dataset
37 changes structure (loader fails fast; we'd switch to a different
opendata dataset that covers the same rows).

## References

- `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/opendata/` — loader + cache.
- `taiwan-fda-mcp/src/taiwan_fda_mcp/tools.py` — `search_drugs` tool.
- `taiwan-fda-mcp/tests/unit/test_search.py` — behaviour tests.
- `.private/docs/sources/data-fda-opendata-analysis.md` — full enumeration
  of the 18+ available opendata datasets.
- `.private/docs/sources/lmspiq-spa-analysis.md` — analysis backing the
  skip-lmspiq decision.
