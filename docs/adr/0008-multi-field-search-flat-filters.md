# ADR-0008: Multi-field `search_drugs` via opendata, with flat AND filters

- **Status**: Accepted
- **Date**: 2026-05-31
- **Extends**: [ADR-0003](./0003-search-via-dataset37-not-lmspiq.md) (search backed by opendata), [ADR-0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md) (flat schema)

## Context

The long-term goal is to let an LLM query Taiwan drug data the way a human uses
the `mcp.fda.gov.tw` website. A 2026-05-31 live investigation of that website
established two facts:

1. **The website search is a CAPTCHA-gated, anti-forgery-token-protected HTML
   form** (`POST /im`) with no JSON API; `/Serv/Query.asmx` exposes only
   `GetDrugDoc`. Real-time per-query search via the site is not viable —
   re-confirming ADR-0003's rejection of website scraping with fresh evidence.
2. **The website's full search surface is already inside opendata we download.**
   Dataset 37's raw JSON has 28 columns; we mapped only 12. The website fields
   `s_indication / s_applicant / s_manufacturer / s_form / s_type` map to columns
   already parsed, and `s_country` maps to `製造廠國別` (present but unmapped).
   `s_atc` needs Dataset 41 (ATC; 80,293 rows, clean `許可證字號` join).

The current `search_drugs(query, search_by)` shape searches **one field at a
time** (`search_by` enum). The website form lets a user fill **multiple fields
at once = AND-combined filtering** — which `search_by` cannot express.
Investigation detail: `.private/docs/sources/website-search-surface-and-dataset-mapping.md`.

## Decision

We expand `search_drugs` to **flat, optional, per-field filter parameters,
AND-combined**, all served from opendata (never website scraping).

### 1. Request shape — flat params, not nested object, not enum

```python
search_drugs(
    query: str = "",          # fuzzy: case-insensitive substring across
                              # name_zh + name_en + ingredient + license_no
    name_zh="", name_en="", ingredient="", indication="",
    applicant="", manufacturer="", form="", drug_class="",
    country="",               # EXACT, case-insensitive (e.g. "TW")
    limit: int = 10,
) -> SearchDrugsResponse
```

- Every provided filter narrows the result; all combine with **AND** (and with
  `query`). Empty params are ignored. At least one of `query` / any filter must
  be non-empty, else an error is returned.
- **`query` is kept** as the fuzzy OR-across-key-fields entry point.
- **`search_by` is removed** — its single-field values are fully subsumed by the
  flat params. This is a deliberate breaking change to the tool's input schema,
  acceptable pre-wide-adoption; the snapshot test freezes the new contract.

### 2. Match semantics

The governing rule, applied per field by the **shape of its values**:

> **Free-text fields → case-insensitive substring. Short code / closed-enum
> fields → case-insensitive exact.**

- **Substring (free text)**: `query`, `name_zh`, `name_en`, `ingredient`,
  `indication`, `applicant`, `manufacturer`, `form`, `drug_class`. These hold
  long human-readable strings a caller cannot reproduce verbatim, so a filter
  must match when the field *contains* it — `name_zh="脈優"` matches
  `脈優錠５毫克`; `ingredient="amlodipine"` matches `AMLODIPINE BESYLATE`. Exact
  match here would make the tool unusable (it would demand the full literal
  string). This is the behaviour a human expects from a search box.
- **Exact (codes / enums)**: `country`. It stores a fixed short code (`TW`,
  `US`, `JP`…); semantics are "is it this country," not "does it contain this
  string." Substring would over-match — `country="T"` would hit `TW`, `AT`,
  `IT`, `PT`. Case-insensitive still holds, so `tw` matches `TW`.

This rule is the precedent for future filters: closed-form codes use
exact/prefix, not loose substring. E.g. P2's `atc` (a code like `C08CA01`)
should be exact, with prefix match when querying a whole class (`C08…`) — never
arbitrary substring; any future fixed-enum filter (e.g. a normalised
`drug_class` code) likewise leans exact.

### 3. Response — collapse duplicate license rows

A license held by N manufacturers currently surfaces as N identical rows. We
**collapse by `license_no`** and expose `manufacturers: list[str]`; a
`manufacturer` filter matches if **any** manufacturer matches. `DrugLicenseRow`
gains `country`; other unmapped columns (e.g. `controlled_level`) may be added
as optional fields without re-litigating this ADR.

### 4. Scope

This ADR covers **P1**: dimensions served by Dataset 37 alone (no new network
source). ATC (`s_atc`, Dataset 41) is **P2**, a later additive `atc` param.
Revoked-drug search (`s_revoke`, Dataset 36) stays v1 Out-of-Scope. The bulk
data-freshness mechanism that all of this rides on is a separate decision
(ADR-0009 — landing in a follow-up PR).

## Consequences

**Positive**
- An LLM can replicate the website's multi-field search (~10 of 13 fields) with
  zero new data sources — just mapping columns already downloaded daily.
- Flat params are self-documenting in the JSON schema (LLM sees every filter);
  consistent with ADR-0006; 1:1 with the website form; P2 extends additively.
- The long-standing duplicate-row issue is resolved as a side effect.

**Negative / accepted trade-offs**
- Removing `search_by` breaks the existing input-schema contract — requires a
  snapshot regen and a CHANGELOG note.
- ~10 optional params enlarge the tool signature and docstring (mitigated by
  per-param descriptions; this is the website's own dimensionality).
- AND-only (no OR across filters) in v1 — the website's multi-value/OR niches
  (`s_material` multi-ingredient, `rdType`) are deferred.

**Neutral**
- `query`'s fuzzy "any" behaviour is unchanged; only its `search_by` siblings go.

## Verification

- `search_drugs` JSON schema (snapshot) lists `query` + the flat filter params
  and **no** `search_by`.
- `search_drugs(manufacturer="台灣", form="錠劑", country="TW")` returns only rows
  matching all three; `country="tw"` matches `TW` (case-insensitive exact).
- A license with multiple manufacturers returns **one** row with a populated
  `manufacturers` list.
- Revisit if: usage shows AND-only is too restrictive (add OR), a niche website
  filter proves frequently needed, or Dataset 37 drops/renames a mapped column.

## References

- `.private/docs/sources/website-search-surface-and-dataset-mapping.md` — the live investigation + full mapping table + ratified sub-decisions.
- Code: `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/opendata/{search.py,dataset37.py}`, `tools.py` (`search_drugs`), `tool_responses.py` (`DrugLicenseRow`, `SearchDrugsResponse`).
- [ADR-0003](./0003-search-via-dataset37-not-lmspiq.md), [ADR-0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md).
