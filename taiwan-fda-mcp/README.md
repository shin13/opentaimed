# taiwan-fda-mcp

MCP server exposing Taiwan FDA drug information (仿單 + 許可證 metadata) to any MCP-compatible client.

> [!IMPORTANT]
> **This is NOT an official Taiwan FDA product.** It is an independent
> open-source wrapper around public TFDA APIs (`mcp.fda.gov.tw` GetDrugDoc +
> `data.fda.gov.tw` opendata). The TFDA does not endorse, review, or maintain
> this software. Data is reproduced from upstream TFDA endpoints without
> modification of clinical content, but **always verify against the official
> source** (`source_url` / `human_url` are returned with every response)
> before any clinical decision. This software is not a medical device and
> not a clinical decision support system.

## Tools

- `search_drugs(query, name_zh, name_en, ingredient, indication, applicant, manufacturer, form, drug_class, country, limit)` — multi-field license search over Dataset 37. All filters optional and AND-combined; free-text fields match by case-insensitive substring, `country` by exact code. Duplicate-manufacturer rows collapse into one result with a `manufacturers` list.
- `get_package_insert(license_no, fields)` — fetch 仿單 sections + citation metadata
- `check_insert_updates(since_date, license_list?)` — find inserts updated since the given date

## Install

The server speaks MCP over stdio. There are two ways to run it.

### Recommended — `uvx` (zero clone)

> [!NOTE]
> Available once the first release is published to PyPI (see
> [`CHANGELOG.md`](../CHANGELOG.md)). Until then, use the **from source** path
> below. `uvx` downloads, runs in an ephemeral environment, and self-updates —
> no manual install step. The dataset cache lives in your per-user OS cache dir,
> not the ephemeral package tree, so it survives across runs.

```bash
uvx taiwan-fda-mcp-server
```

### From source (development / pre-release)

```bash
uv sync
cp .env.example .env  # optional — defaults work for public FDA APIs
uv run taiwan-fda-mcp-server
```

## Connect from an MCP client

Each client wires the same server in its own config format. The three tools
appear after a restart/reload; ask in Chinese, e.g. `脈優錠 5mg 的用法用量？`.

### Claude Code

```bash
# uvx (after PyPI release)
claude mcp add taiwan-fda -- uvx taiwan-fda-mcp-server

# from source
claude mcp add taiwan-fda -- uv run --directory /absolute/path/to/taiwan-fda-mcp taiwan-fda-mcp-server
```

### Claude Desktop

Add to your config (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "taiwan-fda": {
      "command": "uvx",
      "args": ["taiwan-fda-mcp-server"]
    }
  }
}
```

From source, swap the `command`/`args` for:

```json
{
  "command": "uv",
  "args": ["run", "--directory", "/absolute/path/to/taiwan-fda-mcp", "taiwan-fda-mcp-server"]
}
```

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.taiwan-fda]
command = "uvx"
args = ["taiwan-fda-mcp-server"]
```

## Test

```bash
uv run pytest                       # unit tests (default)
uv run pytest -m integration        # live FDA API tests
uv run ruff check .
uv run pyright src
```
