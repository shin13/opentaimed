# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Top-level `README.md`, `LICENSE` (MIT), `SECURITY.md`, `CHANGELOG.md`.
- Minimal GitHub Actions test workflow (`.github/workflows/test.yml`).

### Changed
- `taiwan-fda-mcp/README.md` now leads with a non-official-wrapper disclaimer.
- `taiwan-fda-mcp/pyproject.toml` metadata filled in for public release
  (description, license, urls, classifiers).

### Internal-only (not in published repo)
- `STATE.md`, `TODO.md`, `CLAUDE.md`, and `docs/` are gitignored — these are
  working-memory artefacts, not public documentation.

## [0.1.0] — 2026-05-25 — `taiwan-fda-mcp` first public-ready cut

This is the first version of `taiwan-fda-mcp` cleared for public consumption.
Earlier development happened in private; this entry summarises the state
shipped at the cut.

### Added

- MCP server `taiwan-fda-mcp` exposing three tools over stdio:
  - `search_drugs(query, search_by, limit)` — substring search over Taiwan
    drug-license dataset (`data.fda.gov.tw` Dataset 37, ~26K licenses).
  - `get_package_insert(license_no, fields)` — fetch insert sections from
    `mcp.fda.gov.tw` GetDrugDoc XML API. 21 mapped fields covering Rx +
    OTC structure (indication, contraindications, dosage, warnings,
    precautions, side_effects, interactions, excipients, special_populations,
    overdose, clinical_trials, shelf_life, storage_cautions,
    patient_instructions, appearance, pharmacology, manufacturer, …).
  - `check_insert_updates(since_date, license_list?)` — find inserts updated
    since the given date, with per-day histogram.
- Pydantic v2 response schemas (`tool_responses.py`) drive every tool's
  `outputSchema`, providing a stable contract for LLM clients.
- Schema snapshot tests (`tests/unit/test_mcp_schemas.py`) freeze the
  input/output contract; intentional changes regenerated via
  `UPDATE_SNAPSHOTS=1`.
- `unmapped_sections` safety-net field surfaces TFDA XML sections that have
  no wrapper field yet — guards against silent data drop when TFDA adds
  sections to the insert format.
- `attribution` block on every `get_package_insert` response declaring the
  wrapper as independent / non-official.
- License-code mapping for 7 verified Rx prefixes (衛署藥製字 / 衛署藥輸字 /
  內衛藥製字 / 衛部藥製字 / 衛部藥輸字 / 衛部菌疫輸字 / 衛部罕藥製字).
- Server `instructions=` block with MANDATORY RULES enforcing `search_drugs`
  first for any Taiwan drug query — prevents LLM clients from answering
  Taiwan-drug questions from training data (which routinely confuses
  brand-name collisions across markets).
- Bounded retry with exponential backoff on the GetDrugDoc client for
  transient 5xx responses and transport errors (max 2 retries by default;
  4xx and parse errors are deterministic and NOT retried).
- `note` field on every `UnmappedSectionInfo` entry instructing LLM clients
  not to invent mappings between unmapped sections and known fields.

### Fixed (during development; closed before first public cut)

- `mcp.fda.gov.tw` rejects the default `python-httpx` user-agent with HTTP 403;
  client now uses a generic browser UA.
- `mcp.fda.gov.tw` requires all four query keys (`license`, `s_code`,
  `startdate`, `enddate`) present even when blank; missing keys → HTTP 500.
- 1-hour `httpx` timeout was too short for wide date-range requests (20+ MB
  XML responses); default raised to 120 s.
- HTML-entity-encoded section text rendered verbatim; now decoded via stdlib
  `HTMLParser`.

### Known Limitations

- `search_drugs` is backed by Dataset 37 (未註銷藥品許可證) only — does not
  cover de-registered drugs. Re-evaluation deferred until real-user feedback
  shows it matters.
- One license number can correspond to multiple registered manufacturers in
  Dataset 37, causing `search_drugs` to return multiple rows with identical
  `license_no`. The duplicate rows are real upstream data, not a bug.
- `appearance` field is plain text only; embedded base64 images in the FDA
  XML are not yet surfaced.

### Verified Against

- Python 3.13
- macOS 14+, Claude Desktop (stdio transport)
- TFDA endpoints `mcp.fda.gov.tw` and `data.fda.gov.tw` as of 2026-05.

[Unreleased]: https://github.com/shin13/opentaimed/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/shin13/opentaimed/releases/tag/v0.1.0
