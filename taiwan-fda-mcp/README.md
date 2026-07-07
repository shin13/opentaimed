# taiwan-fda-mcp

[![PyPI](https://img.shields.io/pypi/v/taiwan-fda-mcp)](https://pypi.org/project/taiwan-fda-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/taiwan-fda-mcp)](https://pypi.org/project/taiwan-fda-mcp/)
[![Tests](https://github.com/shin13/opentaimed/actions/workflows/test.yml/badge.svg)](https://github.com/shin13/opentaimed/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/shin13/opentaimed/blob/main/LICENSE)

Look up official **Taiwan FDA (TFDA)** drug information — package inserts (仿單)
and drug-license data — directly from your AI agent.

**English below · [前往繁體中文說明 ↓](#繁體中文)**

![How it works: you ask your AI agent in plain language, taiwan-fda-mcp queries the official TFDA APIs, and you get an answer with citations.](https://raw.githubusercontent.com/shin13/opentaimed/main/taiwan-fda-mcp/docs/architecture.svg)

> **This is NOT an official Taiwan FDA product.** It is an independent,
> open-source tool that reads the *public* TFDA APIs (`mcp.fda.gov.tw` and
> `data.fda.gov.tw`). The goal is a **reliable, source-cited** way to look up
> Taiwan drug information: every answer links back to the official TFDA page.
> Always verify there before any clinical decision. This is not a medical device.

## What is it?

An MCP server. It gives an AI agent (Claude Desktop, Claude Code, Codex, …)
the ability to search Taiwan's official drug database and read package inserts.
You ask a question in plain Chinese; the assistant fetches the real TFDA data and
answers — with a link to the source.

## What can it do?

It adds four tools to your assistant:

- **`search_drugs`** — find a drug by Chinese/English name, ingredient,
  indication, maker, and more.
- **`search_by_ingredient`** — list every license for an active ingredient,
  grouped into single-ingredient (單方) vs combination (複方) products.
- **`get_package_insert`** — read the official 仿單 of one drug license
  (indications, dosage, warnings, side effects…), with a source link.
- **`check_insert_updates`** — list inserts updated since a given date.

You don't call these by hand. You just ask your assistant a question, e.g.:

- What's the dosage for 脈優 (amlodipine)?
- Does Herceptin's package insert carry a black box warning?
- What does the package insert for 綠油精 (an OTC liniment) say?
- Which drugs contain valsartan?
- Which package inserts were updated in the last 3 days?

Behind the scenes it searches → picks the right license → quotes the insert →
gives you the official TFDA source URL.

## How to install

### Option A — `uvx` (easiest, nothing to clone)

> Available once the first release is on PyPI. Until then, use Option B.

[`uv`](https://docs.astral.sh/uv/) downloads and runs it in a throwaway
environment — no manual install:

```bash
uvx taiwan-fda-mcp
```

### Option B — from source

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp
uv sync
uv run taiwan-fda-mcp
```

## How to use it (connect your AI agent)

Add the server to your client's config, restart it, then just ask a drug
question in Chinese. The four tools appear automatically.

**Claude Code**

```bash
claude mcp add taiwan-fda -- uvx taiwan-fda-mcp
# from source:
claude mcp add taiwan-fda -- uv run --directory /absolute/path/to/taiwan-fda-mcp taiwan-fda-mcp
```

**Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), then restart:

```json
{
  "mcpServers": {
    "taiwan-fda": {
      "command": "uvx",
      "args": ["taiwan-fda-mcp"]
    }
  }
}
```

From source, use `"command": "uv"` with
`"args": ["run", "--directory", "/absolute/path/to/taiwan-fda-mcp", "taiwan-fda-mcp"]`.

**Codex CLI** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.taiwan-fda]
command = "uvx"
args = ["taiwan-fda-mcp"]
```

### Example

Ask your assistant:

> What's the dosage for 脈優 (amlodipine)?

It replies with the dosage section from the official 仿單, plus the TFDA source
link so you can check it yourself.

## Deployment (shared HTTP service — institutional / 院內)

Run one shared instance many agents connect to (ADR-0010 Model B), instead of
each user installing via `uvx`:

```bash
cp .env.example .env          # tune INSERT_THROTTLE_MIN_INTERVAL_SECONDS, DATASET37_TTL_HOURS;
                              # set INSERT_CACHE_ENABLED=true to cut repeat insert egress (ADR-0011)
# place your hospital internal-CA cert at ./certs/{cert,key}.pem
docker compose up -d --build
```

- TLS terminates at the Caddy edge; the MCP container speaks plain HTTP on an
  internal network and is **not** reachable except through the proxy.
- Single worker / single instance only (do not scale replicas — shared in-memory
  state is per-process; Redis is required first; see ADR-0010).
- Egress firewall must allow `mcp.fda.gov.tw` and `data.fda.gov.tw`.
- Internal network only (Stage 1). Public exposure is a separate hardened step
  (ADR-0010 Stage 2: auth + allowlist + abuse protection).
- Client URL: `https://<your-host>/mcp/` (trailing slash). Claude Desktop uses
  Custom Connectors / `mcp-remote`, never a raw `url` in its config file.

---

## 繁體中文

從你的 AI 代理人直接查詢**台灣食藥署（TFDA）**的官方藥物資訊——仿單與藥品許可證資料。

> **這不是台灣食藥署的官方產品。** 這是一個獨立的開源工具,只讀取 TFDA 的
> *公開* API（`mcp.fda.gov.tw` 與 `data.fda.gov.tw`）。目標是做一個
> **可靠、且每筆都附上出處**的台灣藥物資訊查詢工具:每個回答都會附上 TFDA 官方頁面連結。
> 臨床決策前請務必到官方頁面再次確認。本工具不是醫療器材。

### 這是什麼?

一個 MCP server。它讓 AI 代理人（Claude Desktop、Claude Code、Codex…)能搜尋台灣官方藥物資料庫、讀取仿單。
你用中文問問題,助理就去抓真實的 TFDA 資料來回答,並附上出處。

### 可以做什麼?

它幫你的助理加上四個工具:

- **`search_drugs`** — 用中／英文藥名、成分、適應症、製造商等條件找藥。
- **`search_by_ingredient`** — 列出某成分的所有藥證,依單方／複方分組。
- **`get_package_insert`** — 讀某一張藥證的官方仿單(適應症、用法用量、警語、副作用…),附出處連結。
- **`check_insert_updates`** — 列出某日期之後更新過的仿單。

你不用自己呼叫這些工具,直接問助理就好,例如:

- 脈優的用法用量？
- Herceptin 的仿單有沒有加框警語？
- 綠油精的仿單內容？
- 有哪些含 valsartan 的藥？
- 最近 3 天有哪些仿單更新？

它會自動:搜尋 → 選對的藥證 → 引用仿單 → 給你 TFDA 官方來源網址。

### 怎麼安裝

**方法 A — `uvx`(最簡單,不用 clone)**

> 等第一版發布到 PyPI 後即可使用;在那之前請用方法 B。

[`uv`](https://docs.astral.sh/uv/) 會在用完即丟的環境下載並執行,不需手動安裝:

```bash
uvx taiwan-fda-mcp
```

**方法 B — 從原始碼**

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp
uv sync
uv run taiwan-fda-mcp
```

### 裝好之後怎麼用(連到你的 AI 代理人)

把 server 加進你工具的設定檔,重新啟動,然後直接用中文問藥物問題,四個工具會自動出現。

**Claude Code**

```bash
claude mcp add taiwan-fda -- uvx taiwan-fda-mcp
# 從原始碼:
claude mcp add taiwan-fda -- uv run --directory /絕對路徑/到/taiwan-fda-mcp taiwan-fda-mcp
```

**Claude Desktop** — 編輯 `~/Library/Application Support/Claude/claude_desktop_config.json`(macOS),然後重新啟動:

```json
{
  "mcpServers": {
    "taiwan-fda": {
      "command": "uvx",
      "args": ["taiwan-fda-mcp"]
    }
  }
}
```

從原始碼安裝時,把 `command` 改成 `"uv"`、`args` 改成
`["run", "--directory", "/絕對路徑/到/taiwan-fda-mcp", "taiwan-fda-mcp"]`。

**Codex CLI** — 加到 `~/.codex/config.toml`:

```toml
[mcp_servers.taiwan-fda]
command = "uvx"
args = ["taiwan-fda-mcp"]
```

### 範例

問你的助理:

> 脈優的用法用量？

它會回給你官方仿單裡的用法用量章節,並附上 TFDA 來源連結讓你自己核對。

---

## Development · 開發

For tests, linting, and type-checking, see the repository:
<https://github.com/shin13/opentaimed>

```bash
uv run pytest          # unit tests
uv run ruff check .    # lint
uv run pyright src     # type-check
```

License: MIT (with clinical disclaimer) ·
[Changelog](https://github.com/shin13/opentaimed/blob/main/CHANGELOG.md)
