# Architecture Decision Records

This directory records non-obvious decisions about OpenTaiMed's architecture
and operating model. Each ADR captures the **context that forced a choice**,
the **decision** taken, the **consequences accepted**, and how a future
reader can **verify** the decision is still in force.

## Why ADRs exist here

OpenTaiMed is developed with AI coding agents in the loop. A well-grounded
agent reads ADRs before proposing changes, which prevents re-litigating
decisions every few sessions. ADRs are the durable counterpart to the
session-state notes kept in `.private/`.

## Conventions

- Filename: `NNNN-short-slug.md`, zero-padded to four digits.
- Status moves: `Proposed → Accepted → (Deprecated | Superseded)`.
- Superseding an ADR does not delete the old one. Set its status to
  `Superseded by ADR-NNNN`, write a new file pointing back at the old.
- Use the [`_template.md`](./_template.md) as the starting point.
- Keep each ADR to roughly one screen of body text. If it grows past
  that, the decision is too large — split it.

## Index

| # | Title | Status |
|---|---|---|
| [0001](./0001-tfda-dual-api-strategy.md) | TFDA dual-API strategy for drug data | Accepted |
| [0002](./0002-mandatory-rules-server-instructions.md) | Server instructions must be directive, not descriptive | Accepted |
| [0003](./0003-search-via-dataset37-not-lmspiq.md) | `search_drugs` is backed by opendata Dataset 37, not lmspiq scraping | Accepted |
| [0004](./0004-mit-license-with-clinical-disclaimer.md) | MIT License with a separate informational clinical disclaimer | Accepted |
| [0005](./0005-private-nested-repo-pattern.md) | `.private/` as a nested independent git repository | Accepted |
| [0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md) | Flat response schema for `get_package_insert`, aligned with healthcare MCP norms | Accepted |
| [0007](./0007-rx-otc-dual-format-full-fidelity.md) | Rx/OTC dual-format support with full-fidelity insert return | Accepted |
| [0008](./0008-multi-field-search-flat-filters.md) | Multi-field `search_drugs` via opendata, with flat AND filters | Accepted |
| [0009](./0009-distributed-install-data-freshness.md) | Data-freshness strategy for distributed installs | Accepted |
| [0010](./0010-http-transport-hosting-model.md) | HTTP transport and staged hosting model (internal → external) | Accepted (Stage 1) |
| [0011](./0011-opt-in-package-insert-cache.md) | Opt-in package-insert cache | Accepted |
