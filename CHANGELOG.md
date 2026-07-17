# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI: daily live TFDA smoke test (`smoke.yml`) and weekly `pip-audit` dependency
  CVE scan (`audit.yml`), each auto-filing a deduped GitHub issue on failure.
- CI: `.github/dependabot.yml` enables routine weekly version-update PRs for the
  `uv` Python deps and adds the `github-actions` ecosystem, which keeps the new
  commit-SHA action pins current. (Dependabot security updates already ran
  without this config.)

### Security
- Lightweight v1 security review (`docs/security-review-2026-07.md`) walking the
  10 CLAUDE.md security invariants against the shipped read-only surface.
- CI: every GitHub Actions `uses:` is now pinned to a full commit SHA (with a
  `# vX.Y.Z` comment) instead of a mutable version/branch tag — supply-chain
  hardening. Pins target the latest Node 24 action majors (checkout v7,
  setup-uv v8, upload-artifact v7, download-artifact v8, gitleaks-action v3,
  gh-action-pypi-publish v1.14.0), removing the Node 20 deprecation warning
  ahead of GitHub's 2026-09-16 Node 20 runtime removal.

## [0.6.0] — 2026-07-07

### Changed
- **Dataset 37 search index now uses over-TTL blocking refresh** (ADR-0012,
  supersedes ADR-0009). A query past `DATASET37_TTL_HOURS` (24) blocks on one
  bounded refresh (new `DATASET37_REFRESH_TIMEOUT_SECONDS`, default 15 s) and
  serves fresh data; on timeout/failure it serves the last-good snapshot with
  `is_stale=True` and retries in the background (≤3). A single-flight lock means
  concurrent stale callers trigger one download. Live measurement showed the
  download is sub-second, so the freshest-data default costs <1 s in practice
  while removing the stale-by-default window. `is_stale=True` now means precisely
  "past TTL and a live refresh could not complete." No response-schema shape
  change (only the `is_stale` description tightened).

## [0.5.0] — 2026-07-07

### Added
- **`search_by_ingredient` tool** — lists every Taiwan FDA license for an active
  ingredient, grouped into single-ingredient (單方) vs combination (複方) products
  by verbatim 主成分略述 signature. Splitting is on `;;` only (empirically the sole
  real combination delimiter in Dataset 37 — `+` and `,` occur only inside single
  chemical names), and salt forms are preserved exactly, so `AMLODIPINE BESYLATE`
  and `AMLODIPINE BESILATE` form distinct groups: the wrapper reports how TFDA
  registered each license and never decides salt-form equivalence. Response
  carries `total_matched`, `mono_count`, `combo_count`, `group_count`, and
  `groups` (each with `components`, `is_mono`, `count`, and a `limit_per_group`-
  truncated `licenses` list); groups sort 單方-first then by descending count.

## [0.4.0] — 2026-07-02

### Added
- Env-switched HTTP transport (`MCP_TRANSPORT=http`) for the shared institutional
  service (ADR-0010 Stage 1): non-root `Dockerfile`, `docker-compose.yml` with a
  Caddy TLS edge, a `/health` readiness route, and graceful background-task
  shutdown. stdio remains the default — individual `uvx` use is unchanged.
- Opt-in in-memory package-insert cache (ADR-0011): `INSERT_CACHE_ENABLED`
  (default off), `INSERT_CACHE_TTL_HOURS` (6), `INSERT_CACHE_MAX_ENTRIES` (1000),
  `INSERT_CACHE_MAX_MB` (128). Caches raw GetDrugDoc XML per license, re-parsed on
  hit; `get_package_insert` responses gain `from_cache` + `cache_age_hours`
  (`retrieved_at` stays the real fetch time). Empty and unparseable responses are
  never cached, so a transient upstream blip cannot pin a false "not found";
  hit/miss/evict logging with a periodic INFO stats rollup. Cuts repeat egress to
  `mcp.fda.gov.tw` for the shared HTTP service; individual `uvx` users keep
  live-fetch behaviour by default.

## [0.3.0] — 2026-06-09

### Added
- **`check_insert_updates` result cap** — new `limit` parameter (default 200,
  newest-first; 0 disables) bounds the `updates` list, plus `returned` and
  `truncated` response fields. `total` and `by_date` still reflect every
  matched update, so a wide date range that matches thousands of inserts no
  longer floods the response — the caller sees the true scope and can narrow
  `since_date` or raise `limit`.
- **Insert egress throttle** — a process-wide minimum-interval gate on outbound
  `GetDrugDoc` (package-insert) requests, configurable via
  `INSERT_THROTTLE_MIN_INTERVAL_SECONDS` (default 0.5s). Prerequisite for the
  shared HTTP service (ADR-0010): prevents a single deployment from
  concentrating every clinician's lookup onto one egress IP and tripping
  TFDA-side rate limiting. Off when set to 0; individual `uvx` users are
  effectively unaffected.

### Changed
- **`get_package_insert` no longer duplicates `last_update_date`.** It was
  returned both as a top-level field and inside the `fields` map; it is now
  top-level only (saving tokens). Requesting it explicitly via `fields=` is
  still accepted and served top-level — not flagged as an unknown field.

## [0.2.1] — 2026-06-01

### Fixed
- **`uvx` one-line install now works.** Added a `taiwan-fda-mcp` console
  script (matching the package name) so `uvx taiwan-fda-mcp` launches the
  server. The earlier docs showed `uvx taiwan-fda-mcp-server`, which fails:
  `uvx` resolves a bare command to a package of the same name, and there is
  no `taiwan-fda-mcp-server` package. The `-server` entry point is kept as a
  backward-compatible alias. README and all client configs (Claude Code /
  Claude Desktop / Codex) updated to use `taiwan-fda-mcp`.

## [0.2.0] — 2026-06-01 — first public PyPI release

First release published to PyPI. Bundles all work since the internal
`0.1.0` cut: full-fidelity Rx/OTC inserts, distribution + cache-freshness
foundation, flat multi-field search, and the PyPI/uvx packaging.

### Added
- **Full-fidelity Rx + OTC insert coverage** in `get_package_insert`
  (ADR-0006 flat schema + ADR-0007 dual-format):
  - All 21 Rx sub-section fields (§3.x / §5.x / §6.x / §8.x / §10.x),
    individually addressable and citable by section number.
  - `special_warning` (top-level `<WARNING>` = 加框警語 / BBW) and
    `characteristics` (`<CHARACT>`) pre-section fields, with a MUST-quote
    server-instructions rule for `special_warning`.
  - `confirmed_absent` — distinguishes "TFDA structurally confirms no BBW"
    from "tool failed to fetch".
  - `response_format` enum (`concise` / `key` / `detailed` / `full`);
    entity lists (`main_factories` / `sub_factories` / `companies`) and
    image `data_url` payloads surface only on `full`.
  - `format` discriminator (`rx` / `otc`) dispatched from `<DTYPE>`.
  - `additional_sections` (section_no + title + verbatim text) — replaces
    the older `unmapped_sections` safety net.
  - `images` metadata (always) with base64 `data_url` (on `full`); parser
    now retains `<VALUE type="image">` payloads with a mime fallback.
  - OTC field space (`usage` / `usage_precautions` / `directions` /
    `otc_warnings` + shared `ingredients` / `excipients` / `packaging`);
    OTC §3/§5 exposed via stable parents (sub-numbering varies per drug).
  - `available_sections` per-call table of contents over every populated
    section (fixed fields + tail), so clients never assume `fields` is
    exhaustive.
  - `LICENSE_PREFIX_MAP` expanded 7 → 27 prefixes (fixes the OTC-prefix
    crash on `衛署成製字…`).
- MCP Resources `structure://rx-insert` and `structure://otc-insert` —
  TFDA insert structure + field-name maps, lazy-loaded by capable clients.
- Top-level `README.md`, `LICENSE` (MIT + clinical disclaimer),
  `SECURITY.md`, `CHANGELOG.md`.
- Public `CLAUDE.md` — contributor and AI-agent alignment document with
  a "Quick Reference: I want to..." table mapping common tasks to files
  and ADRs.
- `docs/adr/` — Architecture Decision Records (ADR-0001 dual-API
  strategy, ADR-0002 directive server instructions, ADR-0003
  Dataset 37 over lmspiq, ADR-0004 MIT + clinical disclaimer,
  ADR-0005 `.private/` nested repo) + template + index.
- GitHub Actions workflow `test.yml` running ruff + pyright + pytest
  with least-privilege `permissions: contents: read`.
- GitHub Actions workflow `gitleaks.yml` — secret scan on every push,
  every PR, and a weekly scheduled baseline run against full history.
- `.pre-commit-config.yaml` wiring gitleaks as a local pre-commit hook
  for any contributor who runs `pre-commit install`.
- **Distribution & freshness foundation** (ADR-0009; pre-launch Phase 0):
  - Dataset 37 search index now refreshes via **stale-while-revalidate** —
    once warm, a query is served from the cached index immediately while a
    single background task refreshes it, so no tool call blocks on the
    download. Fixes a dead-TTL bug where a long-running server never
    refreshed the index within its TTL.
  - Search index cache moved to the **per-user OS cache dir**
    (`platformdirs`) instead of a working-directory-relative path, so
    ephemeral `uvx` installs keep a durable cache.
  - `search_drugs` responses carry explicit freshness:
    `dataset_retrieved_at`, `dataset_age_hours`, and `is_stale`.
  - Background refresh of Dataset 37 honours
    `FDA_RATE_LIMIT_INTERVAL_SECONDS` — a good-citizen throttle so refreshes
    never hammer `data.fda.gov.tw`.
- Tracked `.claude/settings.json` (`includeCoAuthoredBy: false`) so the
  AI-attribution policy applies to every contributor.
- **Flat multi-field `search_drugs`** (ADR-0008): optional, AND-combined
  per-field filters (`name_zh` / `name_en` / `ingredient` / `indication` /
  `applicant` / `manufacturer` / `form` / `drug_class` / `country`) alongside the
  fuzzy `query`. Free-text fields match by case-insensitive substring;
  `country` by case-insensitive exact. `DrugLicense` gains `country` (from
  `製造廠國別`).
- Duplicate-manufacturer license rows are now **collapsed** into one result
  with a `manufacturers: list[str]` — resolves the known duplicate-row issue.
- **PyPI distribution scaffolding** (pre-launch Phase 2): `.github/workflows/publish.yml`
  publishes to PyPI on a `v*` tag via Trusted Publishing (OIDC — no stored
  token); `tests/unit/test_packaging.py` guards the `taiwan-fda-mcp-server`
  console-script entry point; README gains an `Install` section with `uvx`
  (zero-clone) + from-source paths and Claude Code / Claude Desktop / Codex
  client configs. Not yet published — the package name is reserved on PyPI.

### Changed
- `taiwan-fda-mcp/.env.example` rewritten to match `config.py`: every var
  documented with its default, the cache-dir override commented out (an
  active cwd-relative value would defeat the per-user default), and reserved
  `MCP_TRANSPORT` / `MCP_HTTP_HOST` / `MCP_HTTP_PORT` placeholders added for
  the planned HTTP-service profile.
- AI-assistance disclosure now uses an `Assisted-By:` commit trailer instead
  of `Co-Authored-By:` — keeps disclosure without inflating GitHub's
  contributor graph (documented in `CLAUDE.md`).
- **Breaking (input schema):** `search_drugs` `search_by` parameter removed —
  fully subsumed by the flat filter params. `SearchDrugsResponse` drops its
  `search_by` echo; `DrugLicenseRow.manufacturer` (string) becomes
  `manufacturers` (list) and gains `country`.
- `taiwan-fda-mcp/README.md` now leads with a non-official-wrapper
  disclaimer banner.
- `taiwan-fda-mcp/pyproject.toml` metadata filled in for public
  release (description, MIT license, urls, classifiers, keywords).

### Security
- Repo went public at `github.com/shin13/opentaimed`. Git history was
  rewritten via `git filter-repo` before the public force-push to
  remove pre-public working-memory paths (`STATE.md`, `TODO.md`,
  `CLAUDE.md`, `docs/`) and `Co-Authored-By: Claude` trailers from
  every commit.
- Branch ruleset `Protect main` enforces PR-only updates, required
  status check (`taiwan-fda-mcp`), conversation resolution, blocked
  force-pushes, and blocked deletions. No bypass, including admin.

### Internal-only (not in published repo)
- `STATE.md`, `TODO.md`, `HANDOFF.md`, and the bulk of `docs/` are
  gitignored — these are working-memory artefacts, not public
  documentation. They live in a nested `.private/` git repo (see
  ADR-0005). Only `docs/adr/` is whitelisted into the public tree.

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

[Unreleased]: https://github.com/shin13/opentaimed/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/shin13/opentaimed/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/shin13/opentaimed/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/shin13/opentaimed/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/shin13/opentaimed/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/shin13/opentaimed/releases/tag/v0.2.1
[0.2.0]: https://github.com/shin13/opentaimed/releases/tag/v0.2.0
