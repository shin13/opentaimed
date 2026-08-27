# ADR-0014: NHI drug-item tools (`get_nhi_drug_item`, `list_nhi_drug_items`)

- **Status**: Accepted
- **Date**: 2026-08-27

## Context

TFDA data answers 仿單 questions (indication, dosage, contraindications,
warnings). It cannot answer the questions a pharmacy center actually fields
next: 這顆藥健保給不給付？支付價多少？給付規定在哪一章？Those belong to a
different agency — 衛生福利部中央健康保險署 (NHI, 健保署).

NHI publishes 健保用藥品項查詢項目檔 (open data
`A21030000I-E41001-001`) as a single file carrying all three facts —
reimbursement status, 支付價, and 給付規定 chapter references — plus an
official 健保代碼 ↔ 許可證字號 mapping. Verified live: the mapping resolves
24,014 licIds with **zero unparseable**, so a chart/prescription 健保代碼
resolves to a TFDA licence and onward to a 仿單.

The upstream carries traps that shaped the implementation (spec §2.1):

- The metadata route accepts the **dataset identifier** `A21030000I-E41001`,
  not the resource ID `A21030000I-E41001-001`.
- `modified` and `resourceModified` in the same JSON response disagree by
  **13 days**.
- `numberOfData` (224,553) counts full price history, not current rows.
- The payload is **92 MB** with no ETag, no Last-Modified, and HEAD → 405.
- A 2 KB metadata endpoint exists — a cheap freshness probe the TFDA
  opendata (ZIP-download-only) never offered.

## Decision

We add **two** MCP tools **inside `taiwan-fda-mcp`** (not a new server):
`get_nhi_drug_item(nhi_code)` (forward: 健保代碼 → item) and
`list_nhi_drug_items(license_no)` (reverse: 許可證 → items). Tool count 5 → 7.

Operational details:

- **One server, not two.** The project's boundary principle (from
  `new-mcp-scoping-device-vaccine`) is *split by who uses both in one agent
  task*. 仿單 and 給付 are the **same conversation** — a clinician asking
  "what is this drug and does NHI cover it" needs both in one turn — so they
  live in one server. This is the opposite conclusion from 疫苗/防疫, whose
  intents do not overlap.
- **Two tools, not one with mutually-exclusive params.** The empty result
  means different things per direction: a missing 健保代碼 is usually a typo
  (查無此健保代碼), while a licence with no NHI item is the fact
  此藥未納入健保給付 — which is **42.9%** of active licences (9,570 / 22,297),
  a main path, not an edge case. Each tool therefore carries exactly one
  unconditional flag (`item_on_file`, `nhi_listed`).
- **Current rows including 停止給付.** We ingest the currently-effective rows
  (~45,175), which include delisted items, not only the ~13,916 reimbursed.
  「查無此藥」and「此藥已停止給付」are different clinical answers; dropping the
  latter would make it indistinguishable from a typo. Full price history
  (224,553 rows) is out of scope but not a one-way door — the 92 MB download
  is identical either way, so history is a change of retained projection, not
  of architecture.
- **給付規定 PDF passthrough only.** We surface the official chapter code
  (`payment_rule_sections`, verbatim with trailing dot) and the official PDF
  URL (`payment_rule_urls`), never fetched. The full regulation text belongs
  to the separate `nhi-knowledge-extractor` project; the contract between the
  two is the **official chapter code**, measured at 99% coverage of references.
- **A third refresh policy**, justified by **upstream capability, not
  preference**: Dataset 37 blocks over-TTL (ADR-0012); Dataset 42 serves stale
  while reloading (ADR-0013); NHI **probes the 2 KB metadata, then downloads
  the 92 MB payload in the background**. Only NHI exposes a cheap probe, so
  only NHI can afford this shape. A later reader must not try to unify the
  three — the difference is what each upstream makes possible.
- **The version identity is the payload sha256**, not any upstream timestamp:
  `modified` and `resourceModified` disagree by 13 days in the same response,
  so neither is trusted. `numberOfData` is **not** a swap-in gate either — it
  grows legitimately and the probe-to-download window exceeds 120 s, so gating
  on it would race and could discard newer data.
- **Per-response attribution.** The server now speaks for two agencies, so
  every NHI response carries its own `Attribution` naming 健保署 — distinct
  from the TFDA attribution on insert/appearance responses.

Relationship to [ADR-0003](./0003-search-via-dataset37-not-lmspiq.md): the
licId is parsed out of a URL string in the NHI file; **lmspiq is never
contacted**. No scraping is introduced.

## Consequences

**Positive**
- A citable answer to 給付/支付價/給付規定 questions and an official
  health-insurance-code → licence bridge, without leaving the server.
- The cheap-probe refresh means the 92 MB payload downloads only when the
  metadata actually moved.

**Negative / accepted trade-offs**
- On-disk cache grows ~15 MB → ~45 MB.
- A third refresh policy to understand — mitigated by stating here *why* it
  cannot be unified with the other two.
- A second attribution identity; every NHI code path must attach it.

**Neutral**
- Tool count 5 → 7; two additive schema snapshots, no existing tool's schema
  changed. The `instructions=` string gains an NHI section (per ADR-0002 it is
  not snapshot-tested and is verified against a real client).

## Verification

- Unit tests: `tests/unit/test_nhi_dataset.py`, `test_nhi_client.py`,
  `test_nhi_store.py`, and the NHI cases in `test_tools.py`
  (found / not-found-is-a-fact / whitespace-trim / fan-out / unlisted /
  all-delisted / limit) and `test_mcp_server.py` / `test_mcp_schemas.py`.
- Live contract: `tests/integration/test_nhi_live.py` and the NHI smoke case
  in `tests/integration/test_live_smoke.py` — both probe the 2 KB metadata
  only, never the 92 MB payload.
- The store's two-tier refresh is verified by **mutation**, not by a green
  suite (see CLAUDE.md → Testing & Verification).
- Revisit if: NHI renames the dataset/resource identifiers or the field names;
  a stable upstream version identifier (ETag / true Last-Modified) appears,
  which would let the hash be dropped; or price-history retention is requested,
  which changes the retained projection but not the download.

## References

- Spec: `.private/docs/specs/2026-08-27-nhi-drug-items-design.md`;
  plan: `.private/docs/plans/2026-08-27-nhi-drug-items.md`.
- Implementation: `src/taiwan_fda_mcp/sources/nhi/{client,dataset,store}.py`,
  `sources/license_code.py` (reverse map), `models.py` (`NhiDrugItem`),
  `tool_responses.py` (`NhiDrugItemRow`, `GetNhiDrugItemResponse`,
  `ListNhiDrugItemsResponse`), `tools.py`, `mcp_server.py`, `config.py`.
- Related: [ADR-0001](./0001-tfda-dual-api-strategy.md) (dual-API strategy),
  [ADR-0002](./0002-mandatory-rules-server-instructions.md) (directive
  instructions, not snapshot-tested), [ADR-0003](./0003-search-via-dataset37-not-lmspiq.md)
  (no scraping), [ADR-0012](./0012-dataset37-over-ttl-blocking-refresh.md) and
  [ADR-0013](./0013-dataset42-drug-appearance.md) (the two refresh policies this
  deliberately does NOT reuse).
