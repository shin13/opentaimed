# OpenTaiMed

Open-source tools for **trustworthy Taiwan drug-information lookup** — built on
the public APIs of 衛福部食藥署 (Taiwan FDA / TFDA), made for AI agents.

**English · [繁體中文 ↓](#繁體中文)**

> [!IMPORTANT]
> **OpenTaiMed is NOT an official TFDA product.** It is an independent
> open-source wrapper around public TFDA APIs. The TFDA does not endorse or
> maintain it. Always check the official source before any clinical decision.
> This is not a medical device.

## The idea

AI agents often answer drug questions by guessing from training data — and
a brand name in one country can be a completely different drug in Taiwan.
OpenTaiMed makes the assistant **look up the real TFDA insert and cite it**,
instead of guessing.

> **LLM is a parser, not an author.** Every answer must trace back to official
> TFDA text. If no source is found, it says "未載明" (not documented) — it
> never guesses.

## What's in this repo

| Part | Status | What it is |
|---|---|---|
| [`taiwan-fda-mcp/`](./taiwan-fda-mcp) | **shipped — v0.5.0** | An MCP server with 4 tools. Works with any MCP client (Claude Desktop, Claude Code, Codex…). |
| Web backend | planned | API for non-MCP apps. |
| Web frontend | planned | A query page for clinicians. |

Today `taiwan-fda-mcp` is the only shipped part — start there.

## Try it

Once it's added to your AI agent, just ask a drug question in Chinese. The
easiest install:

```bash
uvx taiwan-fda-mcp
```

Full setup + client configs (Claude Desktop / Claude Code / Codex) →
[`taiwan-fda-mcp/README.md`](./taiwan-fda-mcp/README.md).

## Where the data comes from

- **`mcp.fda.gov.tw`** — official package-insert (仿單) text, live.
- **`data.fda.gov.tw`** — license metadata, ingredients, etc. (cached daily).

Both are public TFDA APIs. No login, no scraping.

## For developers

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp
uv sync && uv run pytest
```

Python 3.13 · [`uv`](https://docs.astral.sh/uv/) · Ruff · Pyright · Conventional
Commits. Report security issues privately — see [`SECURITY.md`](./SECURITY.md),
not public issues.

## License

[MIT](./LICENSE), with a clinical-safety disclaimer in the license file.

---

## 繁體中文

可信賴的**台灣藥物資訊查詢**開源工具,基於衛福部食藥署 (TFDA) 的公開 API,為 AI 代理人打造。

> [!IMPORTANT]
> **OpenTaiMed 不是食藥署官方產品。** 這是獨立的開源工具,只包裝 TFDA 的公開 API。
> 食藥署不背書、不維護本專案。臨床決策前請務必查核官方來源。本工具不是醫療器材。

### 核心理念

AI 代理人常用訓練資料「猜」藥物資訊——而某個國外商品名,在台灣可能是完全不同的藥。
OpenTaiMed 讓助理**去查真正的 TFDA 仿單並附上出處**,而不是用猜的。

> **LLM 是解析器,不是作者。** 每個回答都必須回溯到 TFDA 官方原文;查無資料就說「未載明」,絕不臆測。

### 這個 repo 有什麼

| 部分 | 狀態 | 是什麼 |
|---|---|---|
| [`taiwan-fda-mcp/`](./taiwan-fda-mcp) | **已上線 — v0.5.0** | 有 4 個工具的 MCP server,可搭配任何 MCP client(Claude Desktop、Claude Code、Codex…)。 |
| 後端 Web API | 規劃中 | 給非 MCP 的應用使用。 |
| 前端網頁 | 規劃中 | 給臨床人員的查詢介面。 |

目前只有 `taiwan-fda-mcp` 上線,從這裡開始。

### 試用

把它加進你的 AI 代理人後,直接用中文問藥物問題。最簡單的安裝:

```bash
uvx taiwan-fda-mcp
```

完整安裝與 client 設定 → [`taiwan-fda-mcp/README.md`](./taiwan-fda-mcp/README.md)。

### 資料來源

- **`mcp.fda.gov.tw`** — 官方仿單全文,即時。
- **`data.fda.gov.tw`** — 許可證 metadata、成分等(每日快取)。

兩者都是 TFDA 公開 API,免登入、不爬網頁。

### 給開發者

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp
uv sync && uv run pytest
```

Python 3.13 · uv · Ruff · Pyright · Conventional Commits。回報安全問題請私下進行——
見 [`SECURITY.md`](./SECURITY.md),勿開公開 issue。

### 授權

[MIT](./LICENSE),授權檔內含臨床安全免責聲明。
