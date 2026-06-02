# ADR-0010: HTTP transport and staged hosting model (internal → external)

- **Status**: Proposed
- **Date**: 2026-06-02
- **Extends**: [ADR-0009](./0009-distributed-install-data-freshness.md) (distributed-install freshness)
- **Related**: [ADR-0011](./0011-opt-in-package-insert-cache.md) (insert cache — a Stage 1 prerequisite)

## Context

ADR-0009 defines the **individual** profile: each user runs the stdio MCP server
locally (`uvx taiwan-fda-mcp`), per-user cache, self-contained freshness — Model A,
shipped (Phase 2, v0.2.1).

A second profile is now confirmed-needed: an **institution runs one shared service**
that many agents reach over the network — Model B. One process, one shared cache,
one refresh, instead of every clinician's machine re-downloading data. This needs a
network transport (Streamable HTTP) the stdio-only server does not expose.

Maintainer decisions fixing the design (2026-06-02):

- **(A) Reach = internal *and* external.** The service will be reachable both from
  inside the hospital network and from outside it.
- **(B) Internal CA exists.** The hospital can issue TLS certificates for internal
  hostnames (`*.hospital.internal`).
- **(C) Real demand exists.** This is not speculative — a concrete consumer wants it.

Two forces apply and **push the design to two stages**:

1. **External reach crosses security invariant #8** ("MCP servers do not expose
   ports to the public internet in v1, internal network only") and triggers the
   CLAUDE.md escalation ("open the MCP server to external developers → MCP
   allowlist, trust tiering, dedicated endpoint"). External exposure is therefore a
   deliberate, separately-hardened step — not the default.
2. **Two Model-B-specific risks that do not exist in Model A:**
   - **Shared in-memory state breaks under multiple workers/instances.** ADR-0009's
     SWR memo and single-in-flight refresh guard are per-process; N workers (or N
     containers) ⇒ N caches and N concurrent downloads.
   - **Concentrated egress to TFDA.** Every `get_package_insert` hits
     `mcp.fda.gov.tw` live; a shared service funnels all clinicians' calls through
     one egress IP → TFDA-side rate limiting / IP block takes the whole institution
     offline at once. Mitigated by a service-level throttle (🔴 in `TODO.md`) and an
     opt-in cache ([ADR-0011](./0011-opt-in-package-insert-cache.md)).

## Decision

We add an env-switched HTTP transport and ship hosting in **two stages**, keeping
stdio the default and keeping TLS/auth out of the application.

### Core (both stages, and any cloud/AWS deployment)

1. **Env-switched transport, stdio default.** `config.py` gains
   `MCP_TRANSPORT: Literal["stdio","http"] = "stdio"`, `MCP_HTTP_HOST`,
   `MCP_HTTP_PORT`, `MCP_HTTP_PATH = "/mcp/"` (placeholders already reserved in
   `.env.example`; invalid `MCP_TRANSPORT` fails at settings load, not mid-request).
   `mcp_server.py:main()` switches: no env → `mcp.run()` (stdio, unchanged);
   `http` → `mcp.run(transport="http", host=…, port=…, path=…)`. Signature confirmed
   against FastMCP docs (context7, 2026-06-02): `mcp.run(transport="http",
   host="127.0.0.1", port=8000)` is valid; `path` defaults to **`/mcp/` (trailing
   slash)** and `transport` options are `stdio` | `http` | `sse` (sse deprecated).
   Re-verify against the pinned FastMCP version at implementation time.
2. **Single worker AND single instance.** Model B runs **one worker process in one
   container** so the ADR-0009 SWR memo + in-flight guard (and the ADR-0011 cache +
   per-key lock) stay correct. Horizontal scaling — multiple workers *or* multiple
   containers for HA — is **forbidden until** the shared in-memory state (refresh
   memo, insert cache, herd lock) is moved to a shared store. Running 2 replicas
   today silently breaks dedup and doubles TFDA egress. The shared store is **Redis**
   (🟡 in `TODO.md`, trigger-gated); the ADR-0011 insert cache is in-process memory
   for v1 and migrates onto the same Redis layer when HA or memory pressure demands.
3. **Stateless HTTP sessions** where FastMCP allows, so concurrent agents do not grow
   per-connection server state on a shared instance.
4. **The service speaks plain HTTP only; TLS terminates at a reverse-proxy edge**
   (traefik / caddy, or a cloud equivalent — AWS ALB / API Gateway / CloudFront).
   The MCP process never holds a certificate or private key. This is what makes the
   app **deployment-agnostic**: hospital host, AWS, or laptop run the identical
   container; only the edge and cert source change.
5. **Container hardening.** `Dockerfile` ends with a non-root `USER` (invariant #3);
   `docker-compose.yml` publishes a port only via the proxy; secrets via docker
   secrets / `.env`, never baked into the image (invariant #9).
6. **Egress allowlist + lifecycle.** The deployment must allow outbound to
   `mcp.fda.gov.tw` + `data.fda.gov.tw` (hospital egress firewalls often block this);
   `main()` handles SIGTERM gracefully (cancel the SWR background task) and exposes a
   cheap health endpoint for the proxy/orchestrator.
7. **Scope unchanged.** Even shared/hosted, the service stays **pharmacy-reference
   only — no PHI, no patient-conditioned queries** (CLAUDE.md "Out of Scope v1"). The
   ADR-0004 clinical disclaimer + ADR-0002 attribution still reach end users via the
   `instructions=` string and the per-response `attribution` block — the relevant
   regression for shared transport.

### Stage 1 — internal (院內), greenlit (pending prerequisites)

- Bound to the **hospital intranet only** — reachable by in-hospital agents, **not**
  the public internet. ("Internal network" here means the hospital LAN, not merely
  the docker-internal network.) This keeps invariant #8 intact.
- TLS at the edge using the **hospital internal CA** (decision B).
- **Prerequisite (must land first):** the TFDA insert-path egress throttle (🔴 in
  `TODO.md`). The opt-in insert cache ([ADR-0011](./0011-opt-in-package-insert-cache.md))
  is recommended alongside for a shared deployment.
- Clients: Claude Code / Codex / Cursor connect with a remote `url`; Claude Desktop
  uses Custom Connectors or the `mcp-remote` bridge — **never** a `url` in Desktop's
  config file (it silently wipes the config). See the client matrix research note.

### Stage 2 — external (對外), planned escalation

- Reachable from outside the hospital → a **public domain name**, so TLS uses a
  **public certificate** (Let's Encrypt / AWS ACM) — simpler than the internal CA and
  the natural choice for this path; the internal CA stays for Stage 1.
- Per the CLAUDE.md escalation, external exposure **requires** (not optional):
  real authentication (not a shared static token), client allowlist / trust tiering,
  request rate-limiting + abuse protection at the edge, and a dedicated endpoint.
- The detailed external-exposure control set may warrant **its own ADR** when Stage 2
  reaches the front of the queue (per the JIT-design principle); this ADR fixes the
  staging and the principles, not Stage 2's full control design.

### Rejected alternative

**TLS inside the MCP service** was rejected: it couples a security-critical,
lifecycle-heavy concern (cert issuance/renewal, private-key custody) to the query
service, re-implements what mature proxies automate, puts the private key in the same
process that parses untrusted vendor insert text (larger blast radius), and dirties
the stdio/http switch with cert config. Edge termination keeps the app
transport-simple, key-free, and portable across hospital/cloud.

## Consequences

**Positive**
- Models A (stdio) and B (HTTP) coexist from one codebase; existing individual users
  are unaffected (no-env default = stdio).
- The app holds no TLS/cert/auth logic → simpler, smaller attack surface, portable to
  any host (hospital or AWS) with only edge/cert changes.
- Staging bounds risk: a low-risk internal service ships to the confirmed consumer
  first; public exposure is a separate, consciously-hardened step.
- Institutions deduplicate cache/refresh load on `data.fda.gov.tw`.

**Negative / accepted trade-offs**
- Operating Model B needs a reverse proxy + container + egress rules — more infra than
  `uvx`; accepted as an institutional deployment.
- Single-worker/single-instance caps throughput and rules out HA replicas until a
  shared state layer is built; accepted (lookups are light; correctness first).
- External (Stage 2) carries real new obligations (auth, abuse, uptime, TFDA fair-use
  on a concentrated IP, disclaimer reach to users who only got a URL) — taken on
  deliberately, gated behind Stage 2 hardening.
- Claude Desktop gets a worse path (bridge/UI, not a clean `url`) — an upstream limit
  we document, not one we can fix.

**Neutral**
- Adds `MCP_TRANSPORT`/`MCP_HTTP_HOST`/`MCP_HTTP_PORT`/`MCP_HTTP_PATH` to `.env`.
- Introduces `Dockerfile` + `docker-compose.yml` as the first deployment artefacts.

## Verification

- No env set → server still launches over stdio (Model A regression guard; unit test
  mocks `mcp.run` and asserts the stdio call).
- `MCP_TRANSPORT=http` → an MCP client connects via URL at `/mcp` and lists the 3 tools.
- The instance runs a single worker in a single container; the cache/in-flight-guard
  invariants from ADR-0009 (and ADR-0011) hold under concurrent requests.
- The MCP service source contains no certificate / private-key / TLS code — TLS
  appears only in the proxy/compose layer.
- Stage 1 binds the hospital LAN only; no port is reachable from the public internet.
- The TFDA insert egress throttle (🔴 prerequisite) is present and unit-tested before
  Stage 1 ships.
- Revisit when: Stage 2 reaches the front (likely its own ADR for auth/allowlist/abuse),
  horizontal scaling is needed (shared state layer first), or FastMCP changes `run()`.

## References

- Research note: `.private/docs/sources/mcp-client-remote-config-matrix.md`
  (four-client remote-config matrix; Claude Desktop silent-wipe caveat; AWS portability).
- Backlog: `.private/docs/TODO.md` — 🔴 TFDA insert egress throttle (Stage 1 prereq);
  🟡 Redis shared-state layer (unlocks HA / multi-instance).
- Implementation plan: `.private/docs/plans/2026-05-31-pre-launch-distribution-and-hosting.md` (Phase 3).
- Code to change: `taiwan-fda-mcp/src/taiwan_fda_mcp/{config.py,mcp_server.py}`;
  new `taiwan-fda-mcp/{Dockerfile,docker-compose.yml}`; `.env.example` (reserved vars).
- [ADR-0009](./0009-distributed-install-data-freshness.md) — freshness baseline reused
  (per-call SWR; single-instance constraint preserves it).
- [ADR-0011](./0011-opt-in-package-insert-cache.md) — the opt-in insert cache that composes
  with the egress throttle.
- CLAUDE.md — security invariants #3/#8/#9, local-dev pattern #4 (HTTPS via traefik/caddy),
  escalation trigger (external-developer access → allowlist + trust tiering + dedicated endpoint).
