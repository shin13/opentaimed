# ADR-0013: Dataset 42 drug-appearance tool (`get_drug_appearance`)

- **Status**: Accepted
- **Date**: 2026-07-17

## Context

Clinical staff need to answer "what does this pill look like?" for a given drug
— a real pill-misidentification-prevention use case. Before this decision the
only image path was images embedded in the GetDrugDoc insert XML, surfaced by
`get_package_insert` as `images[]`.

A live probe (2026-07-17) showed that path is inadequate as an appearance source
and carried a latent bug:

- **Sparse coverage** — of three live-verified drugs only 脈優 (Norvasc) had an
  embedded image (attached to §1 性狀, not §1.4); the OTC sample had none.
- **MIME bug** — the real 脈優 image carries neither `mimetype` nor `filename`
  (only `encode="1"`), so the parser fell back to `application/octet-stream` and
  emitted a non-rendering `data:` URL. (Fixed separately by magic-byte sniffing.)

Meanwhile **Dataset 42 (藥品外觀)** — a dedicated official opendata appearance
dataset — was unintegrated. Verified live: 6,186 rows, 99% with a working hosted
image URL on `mcp.fda.gov.tw`, 97% with shape/color, 66% with imprint;
`許可證字號` is unique per row; there is **no per-row date** field.

## Decision

We add a fifth MCP tool, `get_drug_appearance(license_no)`, backed by TFDA
Dataset 42. It is **forward-only** (license_no → appearance) and returns official
structured descriptors (`shape`, `color`, `special_dosage_form`, `odor`,
`score_line`, `dimensions`, `imprint_1`, `imprint_2`) plus an official appearance
`image_url`.

Operational details:

- **URL passthrough, not base64.** We return the official hosted `image_url`
  verbatim and never fetch/inline the image bytes — this keeps responses small
  and adds no server-side network dependency. Before emitting, we validate the
  URL is `https` on host `mcp.fda.gov.tw`; anything else becomes `null`.
- **New source module** `sources/opendata/dataset42.py` (field-map + parse +
  on-disk cache), a `fetch_dataset42` client, and an isolated in-memory index
  `AppearanceStore` keyed by `license_no`.
- **Lightweight, non-blocking refresh.** A query past the TTL is served from the
  last snapshot while a single background reload runs; only a cold start with no
  snapshot on disk blocks (once) and raises on failure. We deliberately do NOT
  reuse ADR-0012's over-TTL *blocking* refresh — appearance is not
  safety-time-critical the way license validity is. Config: `DATASET42_CACHE_DIR`
  + `DATASET42_TTL_HOURS` (default 24).
- **未載明 is a first-class result.** Appearance covers only ~24% of active
  licenses, so a miss returns `appearance_on_file: false` (empty descriptors,
  `image_url: null`) — a positive "not documented" fact, not an `error`. Mirrors
  the `confirmed_absent` precedent.
- **Citation** mirrors the dataset-backed `SearchDrugsResponse` precedent:
  `source_url` (Dataset 42 export URL) + `dataset_retrieved_at` + `attribution`.
  Dataset 42's lack of a per-row date is consistent with that precedent.
- **Division of labour.** `get_drug_appearance` is the pill-appearance source;
  `get_package_insert` `images` remain manufacturer-embedded insert images
  (sparse), steered apart in the server `instructions` and tool descriptions.

Explicitly excluded: reverse pill-ID (descriptors → candidate drugs); fetching
image bytes into base64; reshaping `get_package_insert`'s `appearance` field.

## Consequences

**Positive**
- A fast, official, citable answer to "what does this pill look like" without an
  insert fetch, with structured descriptors usable even when no image is shown.
- No large base64 payloads; no new server-side egress host (the client, not the
  server, loads the image URL).

**Negative / accepted trade-offs**
- Partial coverage (~24%) means `appearance_on_file: false` is common — surfaced
  honestly rather than hidden.
- A second cached opendata dataset (extra on-disk state + config knobs) and a
  small duplication of ZIP/JSON handling between `fetch_dataset37` and
  `fetch_dataset42` (a future refactor could extract a shared helper).

**Neutral**
- Tool count 4 → 5; one additive schema snapshot, no existing tool's schema
  changed.

## Verification

- Unit tests: `tests/unit/test_dataset42.py`, `tests/unit/test_appearance_store.py`,
  and the `get_drug_appearance` cases in `tests/unit/test_tools.py`
  (hit / miss-is-not-error / off-host-URL-dropped).
- Live contract: `tests/integration/test_appearance_live.py` (row count > 1000,
  image URLs on `mcp.fda.gov.tw`).
- Revisit if: TFDA changes the Dataset 42 field names/host, coverage materially
  improves (reconsider whether reverse pill-ID becomes worthwhile), or an image
  URL host other than `mcp.fda.gov.tw` legitimately appears.

## References

- Spec: `docs/superpowers/specs/2026-07-17-drug-appearance-design.md`;
  plan: `docs/superpowers/plans/2026-07-17-drug-appearance.md`.
- Implementation: `src/taiwan_fda_mcp/sources/opendata/{dataset42.py,
  appearance_store.py,client.py}`, `tools.py` (`get_drug_appearance`),
  `mcp_server.py`, `tool_responses.py` (`GetDrugAppearanceResponse`).
- Related: [ADR-0001](./0001-tfda-dual-api-strategy.md) (dual-API strategy),
  [ADR-0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md)
  (flat schema), [ADR-0012](./0012-dataset37-over-ttl-blocking-refresh.md)
  (the blocking refresh this deliberately does NOT reuse).
