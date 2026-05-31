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

## Setup

```bash
uv sync
cp .env.example .env  # optional — defaults work for public FDA APIs
```

## Run the MCP server (stdio)

```bash
uv run taiwan-fda-mcp-server
```

## Connect from Claude Desktop

Add to your Claude Desktop config (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "taiwan-fda": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/taiwan-fda-mcp",
        "taiwan-fda-mcp-server"
      ]
    }
  }
}
```

Restart Claude Desktop. The three tools will appear in the tool list. Ask in Chinese, e.g. `脈優錠 5mg 的用法用量？`.

## Test

```bash
uv run pytest                       # unit tests (default)
uv run pytest -m integration        # live FDA API tests
uv run ruff check .
uv run pyright src
```
