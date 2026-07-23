# taiwan-fda-mcp

[![PyPI](https://img.shields.io/pypi/v/taiwan-fda-mcp)](https://pypi.org/project/taiwan-fda-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/taiwan-fda-mcp)](https://pypi.org/project/taiwan-fda-mcp/)
[![Tests](https://github.com/shin13/opentaimed/actions/workflows/test.yml/badge.svg)](https://github.com/shin13/opentaimed/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/shin13/opentaimed/blob/main/LICENSE)

An MCP server that looks up official **Taiwan FDA (TFDA)** drug information —
package inserts (仿單), license metadata, and pill appearance — from your AI
agent, always with a source link.

**English below · [繁體中文完整教學 ↓](#繁體中文完整教學)**

![How it works: you ask your AI agent in plain language, taiwan-fda-mcp queries the official TFDA APIs, and you get an answer with citations.](https://raw.githubusercontent.com/shin13/opentaimed/main/taiwan-fda-mcp/docs/architecture.svg)

> **Not an official TFDA product.** An independent open-source tool over the
> *public* TFDA APIs (`mcp.fda.gov.tw`, `data.fda.gov.tw`). Every answer links
> back to the official page — verify there before any clinical decision. Not a
> medical device.

## Five tools

| Tool | What it does |
|---|---|
| `search_drugs` | Find a license by Chinese/English name, ingredient, indication, maker… |
| `search_by_ingredient` | List every license for an ingredient, grouped 單方 vs 複方 |
| `get_package_insert` | Read one license's official 仿單, with a source link |
| `get_drug_appearance` | Pill shape/color/dimensions/score/imprint + official image URL |
| `check_insert_updates` | List inserts updated since a given date |

You don't call these by hand — you ask your assistant in plain Chinese and it
searches → picks the license → quotes the insert → cites the TFDA source URL.

## Install

```bash
uvx taiwan-fda-mcp          # easiest: runs in a throwaway env, nothing to clone
```

From source:

```bash
git clone https://github.com/shin13/opentaimed.git
cd opentaimed/taiwan-fda-mcp && uv sync && uv run taiwan-fda-mcp
```

## Connect your agent

**Claude Code**
```bash
claude mcp add taiwan-fda -- uvx taiwan-fda-mcp
```

**Claude Desktop** — `~/Library/Application Support/Claude/claude_desktop_config.json`, then restart:
```json
{ "mcpServers": { "taiwan-fda": { "command": "uvx", "args": ["taiwan-fda-mcp"] } } }
```

**Codex CLI** — `~/.codex/config.toml`:
```toml
[mcp_servers.taiwan-fda]
command = "uvx"
args = ["taiwan-fda-mcp"]
```

Then just ask, e.g. *"What's the dosage for 脈優 (amlodipine)?"* — you get the
仿單 section plus the TFDA source link.

## Deployment (shared HTTP service — institutional / 院內)

One shared instance many agents connect to (ADR-0010 Model B):

```bash
cp .env.example .env          # tune INSERT_THROTTLE_*, DATASET37_TTL_HOURS;
                              # INSERT_CACHE_ENABLED=true cuts repeat egress (ADR-0011)
# place your internal-CA cert at ./certs/{cert,key}.pem
docker compose up -d --build
```

- TLS terminates at the Caddy edge; the MCP container speaks plain HTTP on an
  internal network, **not** reachable except through the proxy.
- Single worker / single instance only (shared in-memory state is per-process;
  Redis required before scaling — ADR-0010).
- Egress firewall must allow `mcp.fda.gov.tw` and `data.fda.gov.tw`.
- Client URL: `https://<your-host>/mcp/` (trailing slash).

## Development

```bash
uv run pytest          # unit tests
uv run ruff check .    # lint
uv run pyright src     # type-check
```

License: MIT (with clinical disclaimer) ·
[Changelog](https://github.com/shin13/opentaimed/blob/main/CHANGELOG.md)

---

## 繁體中文完整教學

從你的 AI 助理直接查詢**台灣食藥署（TFDA）**的官方藥物資訊——仿單、許可證、藥品外觀，每個回答都附上官方出處。這份教學假設你**完全不懂程式**，一步一步帶你裝好、用起來。

> **這不是食藥署官方產品。** 這是一個獨立的開源工具，只讀取 TFDA 的*公開* API
> （`mcp.fda.gov.tw`、`data.fda.gov.tw`）。每個回答都會附上 TFDA 官方頁面連結，
> **臨床決策前請務必到官方頁面再次確認**。本工具不是醫療器材。

### 這是什麼？能幫我做什麼？

你平常用的 AI 助理（Claude Desktop、Claude Code、Codex…）遇到藥物問題時，常常
是「憑印象」回答——而同一個商品名，在台灣可能是完全不同的成分。這個工具讓助理
**改成去查台灣官方藥物資料庫、讀真正的仿單**，再把答案連同出處給你。

一句話：**它讓 AI 不要用猜的，改成查官方資料並附上出處。**

> **重要原則：查得到才說，查不到就說「未載明」。** 這個工具不會替官方資料「腦補」。
> 如果某個藥沒有某項資料，助理會明講「未載明」，而不是自己編一個——這對臨床安全
> 很重要。

### 第一步：安裝

你只需要先裝一個叫 `uv` 的小工具（它會幫你處理其餘一切），然後一行指令就好。

1. 安裝 `uv`（macOS，打開「終端機」貼上這行）：
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. 裝好後，這個藥物查詢工具**不需要另外安裝**——下一步設定時 `uvx` 會自動下載並執行。

### 第二步：把工具接到你的 AI 助理

依你用的軟體選一種，設定完**記得重新啟動該軟體**，五個工具就會自動出現。

#### 如果你用 Claude Desktop（最常見）

1. 打開設定檔（macOS）：`~/Library/Application Support/Claude/claude_desktop_config.json`
   （用「文字編輯」打開即可；找不到就新建一個）。
2. 貼上以下內容並存檔：
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
3. 完全關閉並重新打開 Claude Desktop。看到工具圖示裡出現 `taiwan-fda` 就成功了。

#### 如果你用 Claude Code（終端機）

```bash
claude mcp add taiwan-fda -- uvx taiwan-fda-mcp
```

#### 如果你用 Codex CLI

編輯 `~/.codex/config.toml`，加入：
```toml
[mcp_servers.taiwan-fda]
command = "uvx"
args = ["taiwan-fda-mcp"]
```

### 第三步：開始問問題（豐富範例）

你**不用**記任何指令或工具名稱，就用平常說話的方式問。以下是各種常見情境，直接照著問：

**① 查用法用量**
> 脈優的用法用量是什麼？

助理會回你官方仿單第 3 節的用法用量，並附上 TFDA 來源連結。

**② 查禁忌症 / 副作用 / 警語**
> 脈優錠的禁忌症有哪些？
> Herceptin（賀癌平）的仿單有沒有加框警語？

有加框警語的藥（如賀癌平）會明確列出；沒有的藥會說「未載明」——這是「官方確認沒有」，不是漏查。

**③ 查藥品外觀（辨識藥丸）**
> 脈優錠 5 毫克長什麼樣子？顏色、形狀、上面有沒有刻字？

助理會回：白色、八邊形、有直線刻痕、標註 VLE 與 AML 5，並附上官方外觀圖連結。
（注射劑等沒有藥丸外觀的藥，會說「未載明」。）

**④ 查某成分有哪些藥**
> 有哪些含 valsartan 的藥？單方和複方分別有哪些？

助理會把該成分的所有許可證列出，並分成單方（只有這個成分）與複方（還含其他成分）。

**⑤ 查非處方藥（OTC）**
> 綠油精的仿單怎麼說？

**⑥ 查最近更新了哪些仿單**
> 最近 3 天有哪些仿單更新過？

### 怎麼看它給的答案

- **出處連結**：每個回答都會附一個 `mcp.fda.gov.tw` 的官方頁面連結，點進去可以自己核對。
- **「未載明」**：代表官方仿單裡沒有這項資料。這是誠實回報，不是工具壞掉。
- **藥品外觀**：形狀／顏色／刻痕等來自官方外觀資料庫，只涵蓋部分藥品；查無就說未載明。

### 疑難排解

- **助理說找不到工具 / 沒反應**：確認設定檔存檔後**有完全重新啟動**該軟體。
- **第一次很慢**：`uvx` 首次會下載工具，之後就快了。
- **查某個藥是空的**：可能該藥非現行有效許可證（本工具只查有效藥證），或該欄位官方未載明。
- **公司／醫院網路**：需允許連到 `mcp.fda.gov.tw` 與 `data.fda.gov.tw`。

### 授權與免責

MIT 授權（授權檔內含臨床安全免責聲明）。本工具僅供資訊查詢，不取代專業判斷，
臨床決策前請以 TFDA 官方頁面為準。
