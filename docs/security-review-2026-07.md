# Security Review — `taiwan-fda-mcp` v1 (2026-07)

> **Point-in-time, lightweight review.** Scope is the shipped v1 MCP server
> (`taiwan-fda-mcp`, v0.6.0). This is not a full threat model — a formal threat
> model is Stage-2 work triggered by external exposure (see
> [CLAUDE.md → Trigger Conditions for Architectural Escalation](../CLAUDE.md)).
> It walks the 10 [Security Invariants](../CLAUDE.md#security-invariants)
> against the *actual* v1 surface and records what is enforced, what is not yet
> applicable, and what to watch. Companion to the automated
> [`audit.yml`](../.github/workflows/audit.yml) dependency scan.

_Reviewed at `main` around v0.6.0. Reviewer: maintainer + AI assist._

## 1. Scope & threat model (v1)

`taiwan-fda-mcp` is a **read-only wrapper** over two public, unauthenticated
TFDA HTTP APIs (`mcp.fda.gov.tw` GetDrugDoc + `data.fda.gov.tw` opendata
Dataset 37). It exposes four MCP tools over **stdio by default**; an optional
**internal-only** HTTP transport exists (ADR-0010, Caddy TLS edge, no public
port). What v1 is **NOT**:

- **No authentication / sessions / user identity** — nobody logs in.
- **No PHI / patient context** — no query is conditioned on patient identity
  (explicit v1 out-of-scope, CLAUDE.md).
- **No vendor uploads / user-writable data** — all content is official TFDA
  data fetched live; nothing is written back upstream, and users cannot upload
  insert text in v1.
- **No database / persisted user data** — the only state is an in-process,
  opt-in cache of official insert XML (ADR-0011) and the Dataset-37 search
  snapshot (ADR-0012), both re-fetched from TFDA.

**Trust boundary.** All data crossing into the system is official TFDA content
read over public HTTPS. The **only untrusted input** is the LLM-supplied
argument set: the query string, active-ingredient string, and license number.
These flow to the upstream APIs strictly as **URL-encoded query parameters**
(`httpx` `params=`), never interpolated into a URL path or a shell — so the
untrusted-input surface is a single, well-contained channel.

## 2. Security Invariants (CLAUDE.md) vs the v1 surface

| # | Invariant | Status | Evidence |
|---|---|---|---|
| 1 | Session tokens in httpOnly cookies only | **N/A-v1** | No auth/sessions/cookies exist in v1. Applies when the planned backend/frontend land. |
| 2 | Citation rendering via structured component tree; no `innerHTML` | **Enforced (by construction)** | MCP tools return **Pydantic `BaseModel`** objects, never HTML — `tool_responses.py` (`SearchDrugsResponse`, `GetPackageInsertResponse`, `SearchByIngredientResponse`, `CheckInsertUpdatesResponse`). Repo-wide grep for `innerHTML` / `text/html` / `Markup` / `dangerously*` → **zero hits**. No HTML/rendering surface exists. |
| 3 | Prompts wrapped; indirect-injection defence on external content | **Partially-v1 (surface not yet exercised)** | v1 serves **only official TFDA content** (no vendor-uploaded insert text), so the `trust="external"` path is not yet reached. The server `instructions=` MANDATORY-RULES (ADR-0002) already constrain LLM behaviour (`mcp_server.py:48`). Full prompt-wrapping lands with vendor uploads (future). |
| 4 | No secrets in system prompt | **Enforced** | The `instructions=` block (`mcp_server.py:48`) contains only public endpoint names and workflow rules — no secrets, keys, or internal URLs. gitleaks CI + pre-commit guard against accidental secret commits. |
| 5 | CORS never `*` | **N/A-v1** | No browser origin. HTTP transport is internal-only (ADR-0010); stdio has no CORS surface. |
| 6 | Nonce-based CSP | **N/A-v1** | No web surface served by v1. |
| 7 | Static tool descriptions; never LLM-generated schema | **Enforced** | Tool schemas come from static `@mcp.tool` docstrings + a static `instructions=` string (`mcp_server.py`); frozen by snapshot tests in `tests/unit/test_mcp_schemas.py` (a schema change requires explicit `UPDATE_SNAPSHOTS=1` regeneration — no runtime/LLM generation path). |
| 8 | MCP not exposed to the public internet | **Enforced** | stdio is the default transport. Optional HTTP runs behind an internal-only Docker network + Caddy TLS edge with **no public app port** (ADR-0010; README "Deployment"). Only a `/health` GET is exposed on the internal listener (`mcp_server.py:326`). |
| 9 | Secrets via `.env` / docker secrets, never hardcoded | **Enforced** | `.env` is gitignored; `.env.example` holds no secrets (config knobs only). Base URLs are non-secret public endpoints. gitleaks scans push/PR + weekly full-history baseline. |
| 10 | Query logs stored separately from `user_id`; audit trail on admin reads | **N/A-v1 (no user identity)** | No user identity or query-log store exists in v1. Logs are structured JSON to **stderr** (`logging_config.py`; stdout is reserved for the MCP stdio protocol). See **Finding F1** — the CLAUDE.md convention claims field-masking that is not implemented; harmless in v1 (no sensitive fields) but should be reconciled. |

## 3. MCP-specific surface

- **Untrusted input handling.** License number / query / ingredient strings reach
  the upstream APIs only as URL-encoded query params (`sources/insert/client.py`
  builds `url = f"{base_url}{_PATH}"` with a **constant** path and passes user
  values via `client.get(url, params=params)`; same shape in
  `sources/opendata/client.py`). **No SSRF** — base URLs are config constants
  (`config.py:22-23` `FDA_INSERT_BASE_URL` / `FDA_OPENDATA_BASE_URL`), not
  user-controlled. **No shell/path interpolation** of untrusted input.
- **Egress throttle** (ADR-0010) — a process-wide min-interval gate on the
  GetDrugDoc fetch (`sources/insert/throttle.py`) caps how hard a shared HTTP
  deployment can hit TFDA from one egress IP, protecting both TFDA and the
  service from an IP block. Availability control, not a confidentiality one.
- **Insert cache** (ADR-0011) stores only **official TFDA XML**, keyed by license
  code; a cache hit does not widen the injection surface (cached content is the
  same official data, never vendor input). Off by default.
- **Default browser User-Agent** on the GetDrugDoc client avoids a gov-side 403
  on the default `python-httpx` UA (`sources/insert/client.py`). This is a
  compatibility measure, **not** a security control.

## 4. Dependency & supply-chain posture

- **Dependabot** — version-bump PRs; as of this review **all 18 historical
  alerts are `fixed`, 0 open** (the last HIGH, joserfc `GHSA-gg9x-qcx2-xmrh`,
  was closed by the bump to 1.6.8).
- **`pip-audit`** (new, `audit.yml`) — weekly CVE scan of the exact **locked
  runtime** set (`uv export --no-dev`), complementing Dependabot. Verified clean
  locally against the synced environment at review time ("No known
  vulnerabilities found").
- **Lockfile discipline** — `uv.lock` pinned; CI uses `uv sync --frozen`.
- **Secret scanning** — gitleaks on push/PR + weekly full-history baseline.
- **Deferred hardening (Stage-2 triggers, CLAUDE.md escalation table):** pin
  GitHub Actions by commit SHA; distroless images + cosign signing (SCA Tier 3).
  Not required for the current read-only, no-secret, internal-only posture.

## 5. Findings & follow-ups

| ID | Severity | Finding | Action |
|---|---|---|---|
| **F1** | Low | **Logging convention vs implementation gap.** CLAUDE.md → Coding Conventions states "Logging is JSON-structured with explicit sensitive-field masking", but `logging_config.py` implements JSON-to-stderr with **no masking layer**. Harmless in v1 (no PHI, no auth, no user identity → no sensitive fields to mask; `insert.fetch.start` logs only public license/date params). | **Accepted for v1.** Reconcile *before* any PHI/auth/user-identity feature lands: either implement the masking filter the convention promises, or scope the convention to "when sensitive fields exist". Ties to the invariant-#10 escalation (PHI trigger). No code change required now. |
| **F2** | Info | **Prompt-wrapping / indirect-injection defence (invariant #3) is designed but not yet exercised**, because v1 has no vendor-uploaded (`trust="external"`) content. | **Watch.** The first feature that ingests third-party insert text (vendor upload) MUST implement the `<source_data trust="external">` wrapping before shipping. Track against the PHI/vendor-upload escalation triggers. |

**No High/Critical findings.** The v1 surface — read-only, no auth, no PHI, no
user-writable data, single well-contained untrusted-input channel, internal-only
optional HTTP — carries no unmitigated High/Critical risk. Neither finding is
durable enough to warrant a new ADR; both are reconciliation items gated on the
existing PHI / external-exposure escalation triggers already recorded in
CLAUDE.md.
