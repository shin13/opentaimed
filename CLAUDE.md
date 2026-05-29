# OpenTaiMed — Project Conventions for Contributors and AI Agents

This is the alignment document for anyone — human or AI — writing code in
this repository. It captures the design principles, conventions, and
invariants that survive across sessions. For the **why** behind specific
structural choices, see [`docs/adr/`](./docs/adr/). For release history,
see [`CHANGELOG.md`](./CHANGELOG.md).

## Project Overview

可被信任的台灣藥物資訊查詢系統，整合官方資料來源，提供臨床人員直接問答，並作為 agentic system 的 MCP 基礎建設。

A trustworthy Taiwan drug-information lookup system. Integrates official
TFDA sources, serves clinical staff directly, and provides MCP
infrastructure for agentic systems. Independent open-source wrapper —
**not** a Taiwan FDA product or medical device.

## Core Design Principle

**LLM 是解析器，不是作者。 LLM is a parser, not an author.**

Every claim in a response must trace back to original text from an
official source. When no source is found, the system says "未載明" (not
documented) — it does not infer, complete, or extrapolate.

Derived rules:

- Not found → "未載明", never inferred.
- Vendor-uploaded insert content is `trust="external"` and always
  passes through indirect-injection defences.
- Every citation MUST carry `source_url`, `retrieved_at`,
  `last_update_date`, and section path.

## Architecture Meta-Rule

**Clean Architecture, kept flexible — clarity > dogma.** Do not force a
four-layer DDD shape onto code that does not need it. Before adding a
layer, ask: "can a new reader understand this module in five minutes?"
If yes, the layer count is right.

## Repository Layout

```
opentaimed/                    # the public monorepo
├── docs/
│   └── adr/                   # architecture decision records (durable why)
├── taiwan-fda-mcp/            # shipped — v0.1.0 MCP server (only current subproject)
│   ├── src/taiwan_fda_mcp/
│   └── tests/
├── .github/workflows/         # CI (least-privilege)
├── CHANGELOG.md
├── LICENSE                    # MIT + clinical disclaimer
├── README.md
├── SECURITY.md
└── CLAUDE.md                  # ← this file
```

Future subprojects (FastAPI backend, Next.js frontend) will live as
siblings of `taiwan-fda-mcp/` under the same monorepo when they begin.

## Quick Reference — "I want to..."

| Task | Start here | See also |
|---|---|---|
| Add a new TFDA tool | `taiwan-fda-mcp/src/taiwan_fda_mcp/tool_responses.py` (add Pydantic shape first), then `tools.py`, then `mcp_server.py` adapter. Run `UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_schemas.py`. | [ADR-0001](./docs/adr/0001-tfda-dual-api-strategy.md) |
| Change the server's `instructions=` string | `taiwan-fda-mcp/src/taiwan_fda_mcp/mcp_server.py` (MANDATORY-RULES block at top). Re-test in Claude Desktop. | [ADR-0002](./docs/adr/0002-mandatory-rules-server-instructions.md) |
| Add a new TFDA license-number prefix | `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/license_code.py` (`LICENSE_PREFIX_MAP`). Verify the 8-digit code returned by GetDrugDoc with a real `curl`. | [ADR-0001](./docs/adr/0001-tfda-dual-api-strategy.md) |
| Investigate a 5xx from upstream | `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/insert/client.py` already retries 5xx + transport errors. Inspect `_logger.warning("insert.fetch.retry", ...)` output. | — |
| Add a data source beyond TFDA | Follow the **Source Expansion Pattern** below. Open an ADR first. | This file, "Source Expansion Pattern" |
| Add an architectural decision | Copy `docs/adr/_template.md` → `docs/adr/NNNN-slug.md`, update the index in `docs/adr/README.md`. | [docs/adr/README.md](./docs/adr/README.md) |
| Run the full check before pushing | `cd taiwan-fda-mcp && uv run ruff check . && uv run pyright src && uv run pytest` | [Testing & Verification](#testing--verification) |

## Tech Stack

### Current (shipped or in active development)

- **`taiwan-fda-mcp`**: Python 3.13+ · [`uv`](https://docs.astral.sh/uv/) · [FastMCP](https://github.com/jlowin/fastmcp) · Pydantic v2 · `httpx` · pytest + respx for HTTP mocking · Ruff + Pyright.

### Planned (not yet implemented — included for forward-design)

- **Backend** (web API for non-MCP clients): FastAPI, OpenAI Agents SDK with a Protocol abstraction layer for vendor swap.
- **Frontend**: Next.js App Router · TypeScript (strict) · Tailwind · shadcn/ui · Biome · pnpm.
- **Auth / DB**: self-hosted Supabase Auth + PostgreSQL with RLS.
- **Edge** (when deployed): Cloudflare Turnstile + Cloudflare Tunnel.

Any reference to "the backend" or "the frontend" in this document
describes intent, not current code.

## Key Technical Facts (Public TFDA APIs)

OpenTaiMed never scrapes. Both data sources below are public,
unauthenticated HTTP APIs. Division of responsibility is explained in
[ADR-0001](./docs/adr/0001-tfda-dual-api-strategy.md).

### 1. Real-time insert text — `mcp.fda.gov.tw` GetDrugDoc

```
GET https://mcp.fda.gov.tw/Serv/Query.asmx/GetDrugDoc
    ?license={8-digit-code}     # e.g. 衛署藥輸字第021571號 → 02021571
    &s_code={健保代碼}
    &startdate={YYYY/MM/DD}
    &enddate={YYYY/MM/DD}       # range capped at 10 days by the API
```

Returns the 15 standard 仿單 sections + manufacturer + version + last
update date. Section text uses HTML entity encoding (not base64); only
`<VALUE type="image" encode="1">` payloads are base64-encoded images.

### 2. Bulk metadata — `data.fda.gov.tw` opendata (cached daily)

```
GET https://data.fda.gov.tw/data/opendata/export/{id}/json    # ZIP → JSON
```

Relevant datasets:

| ID | Content |
|---|---|
| 37 | Active drug licenses (~26K rows) — the source for `search_drugs` |
| 41 | ATC classification |
| 42 | Appearance (text + base64 images) |
| 43 | Detailed prescription ingredients |
| 53 | Safety bulletins |

No authentication. JSON.

### License code mapping

`衛署藥輸字第021571號` → `02021571`. The full 27-entry prefix table
(ADR-0007 附錄一) is in
`taiwan-fda-mcp/src/taiwan_fda_mcp/sources/license_code.py` — the 衛署
(`0x`/`1x`/`2x`/`41`), 衛部 (`5x`/`6x`/`7x`/`91`), and legacy 內衛
(`12`–`16`) series, covering Rx (`藥製`/`藥輸`), OTC (`成製`/`成輸`),
biologics (`菌疫`), orphan (`罕藥`/`罕菌疫`), and 陸輸 categories.
Representative rows:

| Prefix | Code | Meaning |
|---|---|---|
| 衛署藥製字 | 01 | domestically-manufactured Rx |
| 衛署藥輸字 | 02 | imported Rx |
| 衛署成製字 | 03 | domestic OTC (成藥) |
| 內衛成製字 | 14 | legacy domestic OTC |
| 衛部藥輸字 | 52 | newer imported Rx |
| 衛部成製字 | 53 | newer domestic OTC |
| 衛部菌疫輸字 | 60 | imported biologics |
| 衛部罕藥製字 | 71 | orphan / rare-disease drugs |

Unknown prefixes raise `LicensePrefixUnsupportedError`. Verify any new
prefix with a real `curl` to GetDrugDoc before adding.

## Security Invariants

The following invariants apply across the entire system — including
subprojects that do not yet exist. They are baked into design and must
not be silently dropped when a future component is implemented.

1. **Session tokens** live only in httpOnly cookies (access 15 min,
   refresh with rotation). **No** `localStorage` or `sessionStorage`.
2. **Citation rendering** always goes through a structured React
   component tree. **No** unsafe `innerHTML` or sanitiser-based paths.
3. **Prompts** are wrapped: `<user_query>...</user_query>` for user
   input and `<source_data trust="external">...</source_data>` for any
   third-party content (especially vendor-uploaded insert text).
4. **System prompts** never contain secrets, API keys, or internal
   URLs. Assume the system prompt will leak.
5. **CORS** uses Next.js same-origin rewrites. **Never** set
   `Access-Control-Allow-Origin: *`.
6. **CSP** is nonce-based with `strict-dynamic`. Nonce regenerated
   per-request.
7. **Tool descriptions** use static example queries. **Never** let an
   LLM dynamically generate tool schema.
8. **MCP servers** do not expose ports to the public internet (v1).
   Internal docker network only.
9. **Secrets** flow through `.env` (gitignored) → docker secrets. Never
   hardcoded in source, Dockerfile, or compose files.
10. **Query logs** are stored in a separate table from `user_id`. Admin
    reads leave an `audit_access` trail.

## Coding Conventions

### Python (current)

- 100 % type hints. Pydantic v2 for any data shape that crosses an API,
  tool, or storage boundary.
- `async def` everywhere — even when sync would work today — to keep
  the future agent stack uniform.
- Ruff: line length 100, broad rule selection (see
  `taiwan-fda-mcp/pyproject.toml`).
- Pyright: `typeCheckingMode = "standard"`.
- Every source file opens with two comments:

  ```python
  # path: src/taiwan_fda_mcp/foo.py
  # brief: one-line description
  ```

- Google-style docstrings.
- Absolute imports only. Order: stdlib → third-party → local.
- Custom exceptions inherit from a project base class and carry an
  `RCode` enum value for structured error responses.
- Logging is JSON-structured with explicit sensitive-field masking.

### Frontend (planned, not yet implemented)

- TypeScript strict mode + `noUncheckedIndexedAccess`.
- Path alias `@/*` resolves from `src/`.
- Biome: line length 100, double quotes, semicolons always.
- Feature-based organisation: `src/features/{name}/`.
- State management:
  - Server state → TanStack Query
  - Client state → Zustand
  - Forms → react-hook-form + zod
- Styling: Tailwind + a `cn()` helper for conditional class composition.

### Git

- [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:` /
  `ci:` / `build:`.
- `CHANGELOG.md` follows
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) with an
  `[Unreleased]` slot.
- [SemVer](https://semver.org/) for version numbers.
- PRs require CI green before merge.

### CI/CD

Current:

- `.github/workflows/test.yml` — `uv sync --frozen` → `ruff check` →
  `pyright src` → `pytest -v`. Runs on push to `main` and on every PR.
- `.github/workflows/gitleaks.yml` — gitleaks secret-scan. Runs on
  push, every PR, and a weekly scheduled full-history baseline scan
  (Monday 18:00 UTC = Tuesday 02:00 Taipei).
- Both workflows declare `permissions: contents: read` — least-privilege
  token, cannot push or publish.
- `.pre-commit-config.yaml` wires gitleaks as a local pre-commit hook.
  Contributors run `pre-commit install` once after clone; on macOS
  Tahoe, install pre-commit via `uv tool install pre-commit` rather
  than brew (brew-bundled Python tools get SIGKILL'd by Gatekeeper).

Branch protection: a repository ruleset enforces PR-only updates to
`main` (no direct push, no force-push, no deletion) with required
status check `taiwan-fda-mcp` and required conversation resolution.
No bypass list, including admin.

Planned:

- Dependency audit (`pip-audit` or similar) on a weekly schedule.
- Dependabot for minor / patch dep upgrades.
- Pinned actions by commit SHA rather than version tag.

## Source Expansion Pattern

Adding a new external data source follows three steps:

1. **Deconstruct** — write an analysis of the new source (endpoint
   shape, auth model, payload shape, edge cases). Open an ADR if the
   source affects user-visible behaviour.
2. **Implement** — create a self-contained module under
   `taiwan-fda-mcp/src/taiwan_fda_mcp/sources/{name}/` with its own
   exception types if needed.
3. **Verify** — unit tests with `respx` mocking the HTTP layer; one
   live integration test (marked `@pytest.mark.integration`) confirming
   the upstream contract; one snapshot test if the source contributes
   to a tool's response schema.

## Local Dev Patterns (planned for v0.2+)

These apply when the FastAPI backend / frontend / docker deployment
arrives. The current MCP server runs over stdio and needs none of them.

1. Secrets in `.env` (gitignored), with `.env.example` checked in.
2. Network isolation: split `internal` / `external` docker networks.
3. Non-root containers: every Dockerfile ends with `USER non-root`.
4. HTTPS-first local: mkcert + traefik / caddy serving
   `https://opentaimed.local`.
5. Configuration externalisation: every URL, hostname, and port is an
   environment variable, never a constant in code.

## Trigger Conditions for Architectural Escalation

| Trigger | Required upgrade |
|---|---|
| Use case extends to patient context (PHI) | Switch LLM to Vertex AI `asia-east1` or self-hosted, add a redaction pipeline |
| Formal hospital adoption / customer requires data residency in Taiwan | Migrate hosting to in-hospital infrastructure or a Taiwan-based cloud region |
| Open the MCP server to external developers | Add MCP allowlist, trust tiering, expose via a dedicated `mcp.opentaimed.tw` endpoint |
| Commercial customer or audit requirements | Supply-chain hardening to Tier 3 (distroless images + cosign signing + SCA) |

## Out of Scope (v1)

- **PHI / patient context** — v1 targets pharmacy-center reference
  lookup only. No queries are conditioned on patient identity.
- **健保給付規定** — NHI reimbursement rules are a future RAG
  integration; v1 does not surface them.
- **De-registered drugs** — `search_drugs` is backed by Dataset 37
  (active licenses only). Discontinued drugs are intentionally not
  searchable in v1; see
  [ADR-0003](./docs/adr/0003-search-via-dataset37-not-lmspiq.md).
- **lmspiq.fda.gov.tw scraping** — explicitly rejected in
  [ADR-0003](./docs/adr/0003-search-via-dataset37-not-lmspiq.md).

## Testing & Verification

From `taiwan-fda-mcp/`:

```bash
uv sync --frozen                          # restore exact lockfile environment
uv run ruff check .                       # lint
uv run pyright src                        # type-check
uv run pytest                             # unit tests (no network; ~1 second)
uv run pytest -m integration              # live FDA API calls (only on demand)

UPDATE_SNAPSHOTS=1 uv run pytest tests/unit/test_mcp_schemas.py
                                          # regenerate JSON-schema snapshots
                                          # when tool input/output shape
                                          # intentionally changes
```

Test invariants:

- Snapshot tests in `tests/unit/test_mcp_schemas.py` freeze the
  contract LLM clients see. A schema change requires explicit
  regeneration — there is no automatic update path.
- Server `instructions=` string is **not** snapshot-tested. Verify
  changes manually against a real client (Claude Desktop). See
  [ADR-0002](./docs/adr/0002-mandatory-rules-server-instructions.md)
  for the regression query.

## Where to find more

- **Design rationale** → [`docs/adr/`](./docs/adr/) (start with
  [README](./docs/adr/README.md))
- **Release history** → [`CHANGELOG.md`](./CHANGELOG.md)
- **Security policy** → [`SECURITY.md`](./SECURITY.md)
- **Subproject documentation** →
  [`taiwan-fda-mcp/README.md`](./taiwan-fda-mcp/README.md)
