# OpenTaiMed

Open-source tooling for **trustworthy Taiwan drug information lookup**, built on
top of the public APIs maintained by 衛福部食藥署 (Taiwan FDA / TFDA).

> [!IMPORTANT]
> **OpenTaiMed is NOT an official TFDA product.** It is an independent
> open-source wrapper around public TFDA APIs. The TFDA does not endorse,
> review, or maintain this project. Always verify clinical information
> against the official TFDA source before any clinical decision.
>
> This software is provided as-is under the MIT License and is **not a
> medical device** and **not a clinical decision support system**.

## Why this project exists

LLM-powered drug-information tools routinely answer questions about Taiwan
drugs by guessing from training data — sometimes confidently returning
information about an entirely different active ingredient (a brand name in
the US might be a different drug under Taiwan licensing). OpenTaiMed forces
LLM clients to ground every answer in the actual TFDA insert, with citation
metadata (`source_url`, `last_update_date`, section number) attached to every
clinical claim.

The design principle:

> **LLM is a parser, not an author.** Every statement in a response must
> trace back to original TFDA text. If no source is found, the system says
> "未載明" (not documented) — it does not infer.

## What's in this repo

| Path | Status | Description |
|---|---|---|
| [`taiwan-fda-mcp/`](./taiwan-fda-mcp) | shipped — v0.1.0 | MCP server with three tools (`search_drugs`, `get_package_insert`, `check_insert_updates`) over stdio. Works with any MCP-compatible client (Claude Desktop, etc.). |
| FastAPI backend | planned | Web API surface for non-MCP clients. |
| Next.js frontend | planned | Clinician-facing query UI. |

Currently `taiwan-fda-mcp` is the only shipped subproject. Start there.

## Quick start

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp
uv sync
uv run pytest               # unit tests, no network
uv run taiwan-fda-mcp-server  # stdio MCP server
```

See [`taiwan-fda-mcp/README.md`](./taiwan-fda-mcp/README.md) for full setup,
Claude Desktop wiring, and tool documentation.

## Data sources

- `mcp.fda.gov.tw` GetDrugDoc API — real-time insert sections (15 standard
  sections per Rx insert, with last-update date and version).
- `data.fda.gov.tw` opendata — license metadata, ATC codes, appearance,
  ingredient lists, safety bulletins. Cached daily.

Both are public TFDA endpoints. No authentication is required. This project
does not scrape any TFDA web page.

## Project conventions

- **Python**: 3.13+, managed with [`uv`](https://docs.astral.sh/uv/),
  type-checked with Pyright, linted with Ruff.
- **Tests**: unit tests use `respx` for HTTP mocking; integration tests
  (marked `@pytest.mark.integration`) hit the real TFDA endpoints and are
  excluded from the default run.
- **MCP schema as contract**: tool input/output schemas are snapshot-tested
  (`tests/unit/test_mcp_schemas.py`). Any schema change requires an explicit
  `UPDATE_SNAPSHOTS=1` regeneration step.
- **Conventional Commits** for commit messages.

## Contributing

Issues and pull requests welcome. Please read `SECURITY.md` before reporting
vulnerabilities — do not file security issues in public.

## License

[MIT](./LICENSE). See the additional clinical-safety disclaimer in the
license file.
