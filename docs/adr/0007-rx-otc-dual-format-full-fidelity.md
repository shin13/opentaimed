# ADR-0007: Rx/OTC dual-format support with full-fidelity insert return

- **Status**: Accepted
- **Date**: 2026-05-26
- **Extends**: [ADR-0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md) (flat schema)

## Context

`get_package_insert` was designed against the **prescription-drug (Rx,
處方藥)** insert format only. Investigation (Phase 2 of the
full-structure-coverage work) established that TFDA inserts come in **two
regulator-defined formats** that share the GetDrugDoc XML envelope but
assign **different meanings to the same section numbers**:

- **Rx** — 15 numbered sections + sub-sections, per 衛福部 110.09.14
  衛授食字第 1101407694 號公告 (附件一之一 處方藥仿單格式表).
- **OTC (非處方藥)** — 6 mandated sections, per 衛福部 105.03.08 部授食
  字第 1051402838 號 series (附件一 非處方藥仿單格式), **plus** a
  registrant-variable tail of optional sections (§7+) observed live.

The same `<NO>` collides across formats — Rx §2 = 性狀, OTC §2 = 用途
(適應症); Rx §5 = 警語及注意事項, OTC §5 = 警語. A single field-name
space cannot serve both without semantic corruption. This matters in a
healthcare setting: an Rx 適應症 (clinically-proven indication, physician
decides) and an OTC 用途 (symptom-relief scope, consumer self-selects)
carry different regulatory weight; conflating them under one field name
would let an LLM cite one as the other.

Two authoritative code tables from 《藥品電子仿單交換格式》(衛福部食藥署,
114.09) close the remaining unknowns: 附錄一 (許可證證別代碼對照表) gives
the complete prefix→code map; 附錄二 (藥品類別代碼對照表) gives the
authoritative Rx/OTC classification. These remove all prior guesswork
(the earlier "only 須由醫師處方使用 = Rx" heuristic was wrong — there are
10 distinct Rx 類別 values).

The governing requirement from the project owner: **everything the insert
contains must be returnable** — lossless, full-fidelity. The wrapper is a
parser, not an editor (CLAUDE.md core principle); withholding parsed
official content forces an LLM to either break the agentic flow (redirect
to the website) or hallucinate from training data — the exact failure
mode the tool exists to prevent.

## Decision

### 1. Two field-name spaces, dispatched by 類別

We maintain **disjoint** `_RX_SECTION_NUMBERS` / `RX_FIELDS` and
`_OTC_SECTION_NUMBERS` / `OTC_FIELDS`. `get_package_insert` reads the XML
`<INFO><DTYPE>` (= 藥品類別) and dispatches:

- DTYPE ∈ OTC set `{成藥, 乙類成藥, 甲類成藥, 須經醫師指示使用,
  牙醫師指示使用, 醫師藥師藥劑生指示藥品}` → OTC field space.
- Otherwise → Rx field space.
- Defensive cross-check: §1 title `性狀` ⇒ Rx, `成分` ⇒ OTC. On
  mismatch, log a warning and trust the structural signal.

The response carries `format: "rx" | "otc"` as an explicit discriminator
(ADR-0006 flat shape, single response type — option 1A).

### 2. Field-name policy: distinct where semantics differ, shared where identical

- **Semantically different → distinct names** (forced): OTC `usage` (§2
  用途) ≠ Rx `indication` (§2 適應症); OTC `directions` (§4 用法用量,
  consumer) ≠ Rx `dosage` (§3, clinical); OTC `otc_warnings` (§5 警語) ≠
  Rx `warnings` (§5 警語及注意事項).
- **Semantically identical → shared names**: `ingredients` / `excipients`
  (OTC §1.1/§1.2 ≈ Rx §1.1/§1.2), `packaging` (OTC §6 ≈ Rx §13.1).
- OTC §8 類別 is **not** a separate field — it is the same value already
  exposed at top level as `drug_class`.

### 3. Full-fidelity content return

- **Regulation-fixed sections** (Rx §1-15, OTC §1-6) → named flat fields
  in `fields` (stable, addressable, citeable via `field_sections`).
- **Registrant-variable tail** (OTC §7+; any future Rx §16+) →
  `additional_sections: list[{section_no, title, text}]`, carrying the
  actual text + true section number. Nothing is hidden; new section
  titles flow through without schema churn; content stays citeable.
- `additional_sections` **supersedes `unmapped_sections`**: the old
  safety-net carried `{section_no, title, note}` without text (forcing a
  redirect to `human_url`). Carrying text + true section number is
  strictly more faithful and does not risk mislabeling (the section
  number is real, not a fabricated field mapping).
- **Pre-section blocks**: `special_warning` (top-level `<WARNING>`) and
  `characteristics` (top-level `<CHARACT>`) are independent top-level
  fields. `special_warning` keeps the TFDA term 特殊警語 (≈ boxed /
  black-box warning) — faithful to source, with a MUST-quote server-
  instructions rule and inclusion in `confirmed_absent` when empty.
- **Entity blocks**: `main_factories` / `sub_factories` / `companies`
  surfaced as flat lists (per ADR-0006 leaf models).
- **Title-only divider sub-sections** (empty `<NO>`, title present, no
  text) are preserved as structure in `available_sections` with
  `section_no=""`, never dropped.
- `available_sections` TOC `{number, title, char_count, field_name?}`
  lists every section (mapped + unmapped) for discovery; no text (text
  lives in `fields` or `additional_sections`).

### 4. Images (pending owner confirmation)

`<VALUE type="image" encode="1">` base64 payloads (e.g. §1.4 藥品外觀,
first-aid diagrams) are full-fidelity content but conflict with token
economics. Proposed handling: **image existence is always surfaced** as
metadata `{section_no, caption, mime, size_bytes}` (never silently
dropped); the base64 payload is inlined only under
`response_format="full"` or explicit request — so everything is
retrievable in one call without bloating the default path. **This sub-
decision awaits owner confirmation before implementation.**

### 5. License prefix codes (authoritative, from 附錄一)

`LICENSE_PREFIX_MAP` expands from 7 to the full 27-entry table (see
References). This fixes a real crash: `衛署成製字第…號` previously raised
`LicensePrefixUnsupportedError`.

## Consequences

**Positive**

- An LLM can never cite an OTC 用途 as an Rx 適應症 — the field name
  itself encodes the regulatory distinction.
- Full-fidelity: every parsed section (including safety-critical OTC §10
  急救及解毒方法) is reachable in one tool call.
- Zero schema churn for the OTC variable tail; new §7+ titles just appear
  in `additional_sections`.
- Complete, spec-derived prefix map — no more curl-guessing per prefix.
- Correct Rx/OTC discrimination across all 16 categories, not a broken
  single-value heuristic.

**Negative / accepted trade-offs**

- Two field-name spaces ≈ doubles the docstring and the maintenance
  surface for `get_package_insert`.
- `additional_sections` items are not addressable by a stable English
  field name (filter by title/number instead) — accepted because naming
  a registrant-variable tail would be premature from a 3-sample base.
- Dispatch depends on `<DTYPE>` being populated and correct; the
  structural cross-check is the fallback.
- Images-by-default deferred pending the token-cost decision (§4).

**Neutral**

- `format` discriminator is a small additive field on the existing flat
  response (ADR-0006 unchanged).
- `confirmed_absent` semantics carry over; the OTC space simply never
  lists `special_warning` (OTC has no BBW slot).

## Verification

- `_RX_SECTION_NUMBERS` and `_OTC_SECTION_NUMBERS` exist as separate dicts
  in `tools.py`; no shared mutable mapping.
- `get_package_insert("衛署成製字第007884號")` returns `format="otc"` with
  `usage` / `directions` / `otc_warnings` populated and no `indication` /
  `dosage` / `warnings` keys.
- `get_package_insert("衛署藥輸字第023373號")` returns `format="rx"`.
- OTC §10 急救及解毒方法 text is retrievable via `additional_sections`.
- A live OTC drug with an image surfaces image metadata in the default
  response.

Revisit if: TFDA unifies the two insert formats; a 類別 value appears that
is not in 附錄二; or usage data shows a specific §7+ title is queried
often enough to warrant promotion to a named field.

## References

### 附錄一 — 許可證證別代碼對照表 (license prefix → code; complete)

| Code | Prefix | Code | Prefix | Code | Prefix |
|---|---|---|---|---|---|
| 01 | 衛署藥製字 | 51 | 衛部藥製字 | 12 | 內衛藥製字 |
| 02 | 衛署藥輸字 | 52 | 衛部藥輸字 | 13 | 內衛藥輸字 |
| 03 | 衛署成製字 | 53 | 衛部成製字 | 14 | 內衛成製字 |
| 09 | 衛署菌疫製字 | 59 | 衛部菌疫製字 | 15 | 內衛菌疫製字 |
| 10 | 衛署菌疫輸字 | 60 | 衛部菌疫輸字 | 16 | 內衛菌疫輸字 |
| 19 | 衛署成輸字 | 69 | 衛部成輸字 | | |
| 20 | 衛署罕藥輸字 | 70 | 衛部罕藥輸字 | | |
| 21 | 衛署罕藥製字 | 71 | 衛部罕藥製字 | | |
| 22 | 衛署罕菌疫輸字 | 72 | 衛部罕菌疫輸字 | | |
| 23 | 衛署罕菌疫製字 | 73 | 衛部罕菌疫製字 | | |
| 41 | 衛署藥陸輸字 | 91 | 衛部藥陸輸字 | | |

Code = 2-digit 證別代碼 + 6-digit 許可證號 (zero-padded). Verified live:
02→得安穩 (Rx), 53→安皮露防蚊液 (OTC), 14→綠油精 (OTC).

### 附錄二 — 藥品類別代碼對照表 (Rx/OTC discriminator)

**Rx (處方藥)**: 05 限由醫師使用 · 06 須由醫師處方使用 · 08 由醫師或檢驗師
使用 · 09 限由牙醫師使用 · 11 限麻醉醫師使用 · 12 限由眼科醫師處方使用 ·
15 限由醫師及牙醫師使用 · 18 限由婦產科醫師處方使用 · 25 本藥限神經專科醫
師使用 · A6 本藥須由醫師處方使用(限皮膚科).

**OTC (非處方藥)**: 03 成藥 · 13 乙類成藥 · A3 甲類成藥 · 07 須經醫師指示
使用 · 10 牙醫師指示使用 · 17 醫師藥師藥劑生指示藥品.

> Note: 證別代碼 and 藥品類別代碼 are separate code spaces (e.g. code 12 =
> 內衛藥製 as a 證別, = 限由眼科醫師處方使用 as a 類別). They never collide
> because they are read from different fields.

### Sources

- 衛福部食藥署《藥品電子仿單交換格式》114.09 — 附錄一、附錄二.
- 附件一之一 處方藥仿單格式表 (110.09.14 衛授食字第 1101407694 號).
- 附件一 非處方藥仿單格式 (105.03.08 部授食字第 1051402838 號系列).
- Phase 2 investigation: `.private/docs/sources/otc-insert-xml-analysis.md`.
- Live fixtures: `taiwan-fda-mcp/tests/fixtures/getdrugdoc_otc_sample.xml`.
- [ADR-0006](./0006-flat-response-schema-alignment-with-healthcare-mcp-norms.md) — flat schema this extends.
