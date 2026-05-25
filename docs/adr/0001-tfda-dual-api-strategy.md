# ADR-0001: TFDA dual-API strategy for drug data

- **Status**: Accepted
- **Date**: 2026-05-25

## Context

OpenTaiMed needs two distinct kinds of Taiwan drug data:

1. **Bulk metadata** for substring search — license number, Chinese / English
   name, active ingredient, manufacturer, ATC code, dosage form. Must cover
   the full active-license universe (~26K rows) and respond to free-text
   queries in milliseconds.
2. **Full insert text** for a specific drug — the 15 standard 仿單 sections
   (indication, contraindications, dosage, …) with `last_update_date`,
   citation URL, and version label.

No single TFDA endpoint provides both. Three candidate sources exist:

| Source | What it gives | What it lacks |
|---|---|---|
| `data.fda.gov.tw` opendata Dataset 37 + friends (ATC=41, appearance=42, ingredients=43) | All metadata. JSON ZIP, no auth, daily refresh. | No insert text. |
| `mcp.fda.gov.tw` GetDrugDoc XML API | Real-time insert sections + version metadata. | No list endpoint — caller must already know the 8-digit license code. Date-range query capped at 10 days. |
| `lmspiq.fda.gov.tw` (human search UI) | Free-text search of the human-facing catalogue. | SPA-based; requires JS rendering and HTML scraping. Two known URLs returned 404 from the public internet, suggesting VPN / 院內網路 access. |

Going single-source would either lose search (GetDrugDoc only) or lose
real-time accuracy (opendata only), and scraping lmspiq adds an entire
fragile-by-construction stack (headless browser, anti-bot evasion, network
gating) for content the other two sources already cover.

## Decision

Use both `data.fda.gov.tw` opendata and `mcp.fda.gov.tw` GetDrugDoc in a
clear division of responsibility. Skip lmspiq entirely for v1.

| Tool | Backing source |
|---|---|
| `search_drugs(query, search_by, limit)` | Dataset 37 (cached daily on the client). Substring match over name + ingredient + license. |
| `get_package_insert(license_no, fields)` | GetDrugDoc by 8-digit license code (translated via `sources/license_code.py`). |
| `check_insert_updates(since_date, license_list?)` | GetDrugDoc by date range (no opendata equivalent). |

Both endpoints are public, unauthenticated HTTP. Daily opendata refresh
runs out-of-band of any MCP tool call.

## Consequences

**Positive**
- Zero authentication infrastructure, zero scraping, zero JS rendering.
- Clear contract per source — easy to write a fixture test against either.
- Covers > 90 % of clinical lookup use cases observed in early testing.
- Failures are loud: the wrapper returns a structured error pointing at
  the specific upstream that failed, not a generic "no data".

**Negative / accepted trade-offs**
- `search_drugs` cannot find de-registered drugs — Dataset 37 contains
  only `未註銷` licenses. Old-drug lookup is a known gap; deferred per
  TODO.md "藥品清單 data source 重新評估" until a real user reports it.
- Opendata refresh is at most 24 hours stale. Tolerable for metadata
  (license #, name, ingredient rarely change) but not for insert text —
  which is why insert text goes through real-time GetDrugDoc instead.
- GetDrugDoc occasionally returns transient 5xx (see ADR-0002 follow-up
  in client.py retry logic). The wrapper retries on transport / 5xx;
  4xx and parse errors propagate immediately.

**Neutral**
- One license number can correspond to multiple registered manufacturers
  in Dataset 37, so `search_drugs` may return duplicate-looking rows with
  identical `license_no`. This is real upstream shape, surfaced as-is.

## Verification

- Live smoke test (planned, see TODO) hits both endpoints daily and
  asserts: search returns 脈優 with the expected license, GetDrugDoc for
  that license returns contraindications and `field_sections["contraindications"]="4"`.
- If Dataset 37 schema changes (column rename, encoding shift) the
  daily-cache load fails fast — caught by the smoke test before any tool
  call is served stale data.
- If GetDrugDoc adds new sections, `unmapped_sections` in
  `get_package_insert` response surfaces them — see safety-net logic in
  `tool_responses.py` (`UnmappedSectionInfo`).

Revisit when: a user reports needing de-registered-drug lookup, *or*
lmspiq becomes accessible without VPN (then it might be worth scraping
for the search-by-symptom use case opendata cannot serve).

## References

- `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/opendata/` — Dataset 37 loader.
- `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/insert/client.py` — GetDrugDoc client.
- `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/license_code.py` — Chinese license string → 8-digit code mapping (7 verified Rx prefixes).
- `.private/docs/sources/lmspiq-spa-analysis.md` — analysis backing the skip decision.
