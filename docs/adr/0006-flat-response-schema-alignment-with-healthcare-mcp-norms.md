# ADR-0006: Flat response schema for `get_package_insert`, aligned with healthcare MCP norms

- **Status**: Accepted
- **Date**: 2026-05-25

## Context

When extending `get_package_insert` to cover every TFDA-mandated section
(Rx 15 sections + 8 sub-section groups + 加框警語 pre-section + manufacturer
entities), the obvious next step is "mirror the XML hierarchy in the
response type" — a nested Pydantic tree of `RxPackageInsert →
RxPackageInsertContent → Section06SpecialPopulations.geriatric`. The
appeal is real: built-in citation per leaf, structural
"present vs absent" semantics, schema-as-regulation, snapshot tests that
fail loud on TFDA drift.

We investigated this direction against four sources of evidence before
committing.

**1. Production healthcare MCP servers (4 surveyed, none nest):**

| Server | Response shape | Per-field citation? | "Confirmed absent" semantics? |
|---|---|---|---|
| [`Cicatriiz/healthcare-mcp-public`](https://github.com/Cicatriiz/healthcare-mcp-public) (FDA) | flat envelope + `results[]` | no | no |
| [`openpharma-org/ema-mcp`](https://github.com/openpharma-org/ema-mcp) (EMA) | flat envelope + `results[]` + top-level `source_url`, `last_updated` | no | docstring only |
| [`Augmented-Nature/OpenFDA-MCP-Server`](https://github.com/Augmented-Nature/OpenFDA-MCP-Server) | flat envelope + `results[]` | no | no |
| [`JamesANZ/medical-mcp`](https://github.com/JamesANZ/medical-mcp) | flat envelope + `results[]` | no | no |

Zero precedent for source-document mirroring in healthcare MCP — even
EMA-MCP, which is the closest analogue to our use case.

**2. Anthropic's own published guidance:**

- [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
  recommends a `response_format` enum (`concise` / `detailed`) for
  output-shape control, not nesting.
- [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
  is motivated by the fact that "large structured responses regularly
  blow context budgets" (150k → 2k token reduction case study).
- The `mcp-builder` skill warns: 30 tools with rich schemas eat 3–5 k
  tokens before any conversation starts; the same economics apply to
  deeply-nested response models inlined into `outputSchema`.

**3. FastMCP `outputSchema` implementation reality:**

- [PR #2720](https://github.com/PrefectHQ/fastmcp/pull/2720) — the MCP
  spec requires `outputSchema` root to be `type: "object"`; Pydantic
  emits root `$ref` for self-referential / nested models, breaking the
  spec. FastMCP works around this by **auto-dereferencing all `$ref`s
  and inlining `$defs`** before publishing the schema. A deeply nested
  response model therefore pays its schema cost twice — once at every
  `tools/list`, once on every response.
- [Issue #1784](https://github.com/jlowin/fastmcp/issues/1784) — nested
  Pydantic unpacking is still an open limitation.

**4. Trust-critical features can be achieved flat:**

The features our current design uses to compete on trust — per-field
citation via `field_sections`, gap detection via `unmapped_sections`
with payload-level `note`, third-party identity via `attribution`,
self-correction via `unknown_fields.did_you_mean` — none of them require
nesting. They are all already flat fields, and surveyed peers do not
implement them at all. Our flat schema is already best-in-class on
healthcare-trust dimensions; nesting would not have improved that
ranking, only changed the shape.

## Decision

`get_package_insert` keeps a flat response schema. Trust-critical
healthcare features (BBW prominence, "structurally confirmed absent" vs
"not asked", per-section citation, gap canary) are delivered through
**flat schema enhancements**, not nesting.

We will:

1. **Lift 加框警語 / BBW to a top-level flat field** called
   `boxed_warning`, distinct from `warnings` (§5 一般警語及注意事項).
   Server instructions add a MUST-quote rule for `boxed_warning`. The
   field name is final — `boxed_warning` matches the international term
   "boxed warning" / "black box warning" used in clinical practice.
2. **Lift 特殊性狀 to a top-level flat field** `characteristics`
   (XML `<CHARACT>`), independent of §1 性狀.
3. **Hardcode all 20 Rx sub-section fields as flat names**
   (`geriatric`, `pregnancy`, `mechanism_of_action`, …). The plan's
   Phase 3.1 covers these. No `Section06SpecialPopulations` container
   class. Each sub-section is reachable by its flat name in `fields=`.
4. **Add `confirmed_absent: list[str]`** to the response. Lists any
   schema-mandated optional field whose source XML element exists but
   has no content — i.e. TFDA structurally confirms this drug has no
   such information. Distinguishes "查無 BBW" (tool failed) from
   "TFDA 確認此藥無 BBW" (positive clinical fact). Replaces the
   ad-hoc `if value:` omission for this narrow set of trust-critical
   fields (currently `boxed_warning`, `characteristics`; extensible).
5. **Add `main_factories`, `sub_factories`, `companies`** as flat
   top-level list fields containing simple `{name, address, number}`
   entries. These come from the XML `<MAINFACTORY>` / `<SUBFACTORY>` /
   `<COMPANY>` blocks the parser already extracts but the wrapper does
   not surface.
6. **Add a `response_format` enum parameter** in addition to the
   existing `fields=` selector, with values `concise` / `key` /
   `detailed` / `full`. Anthropic-recommended pattern; `fields=`
   continues to work for fine-grained queries.
7. **Keep nested response models** *only* for genuinely nested
   sub-shapes (an entry in `main_factories` is `{name, address, number}`
   — a small leaf shape, not a hierarchy). No `RxPackageInsert.content`
   wrapper, no `Section0X` container classes.

We will explicitly **not** introduce:

- `RxPackageInsert` / `RxPackageInsertContent` wrapper types.
- Per-section container classes (`Section01Characteristics`,
  `Section05Warnings`, etc.).
- Path-based field selectors (`fields=["section_06.geriatric"]`).
- Per-field `{number, title, text, present}` quadruples on every
  section. (Citation stays in `field_sections`; presence stays in
  `confirmed_absent` for trust-critical fields and in omission for the
  rest.)

This decision binds the plan in
`docs/superpowers/plans/2026-05-24-full-rx-otc-structure-coverage.md`
Phase 3.1 and supersedes any nested-design discussion in earlier session
notes.

## Consequences

**Positive**

- Aligned with the four production healthcare MCP servers we surveyed —
  reduces "surprise factor" for future contributors and for LLM clients
  that have seen those peers.
- Avoids known FastMCP `outputSchema` issues with nested Pydantic
  ([PR #2720](https://github.com/PrefectHQ/fastmcp/pull/2720),
  [Issue #1784](https://github.com/jlowin/fastmcp/issues/1784)).
- Schema bloat from `$ref` auto-dereferencing stays bounded.
- `tools/list` payload stays small, preserving headroom for future
  tools without blowing context budgets on agent startup.
- BBW elevation to flat top-level field still achieves clinical
  prominence — schema-level, not nesting-level.
- `confirmed_absent` gives a positive-signal "no BBW for this drug"
  channel without requiring every section to carry a `present` flag.
- Migration cost: minimal — same `dict[str, str]` shape, additions only,
  no breaking changes to existing flat field names.

**Negative / accepted trade-offs**

- Lose "schema-as-regulation" — the response type does not encode the
  TFDA 110.09.14 公告 structure. Snapshot tests still catch new sections
  via `unmapped_sections`, but a TFDA *removal* (deprecating a §6 sub-
  section, say) is not loud-fail at the type level. Mitigation: keep
  the structure documented in
  `docs/sources/fda-insert-api-analysis.md` and in the planned MCP
  Resource `structure://rx-insert` (Phase 5 of the coverage plan).
- Container sections (§1, §3, §5, §6, §8, §10, §13) retain "ask for the
  parent gets concatenated children text" behaviour. LLMs cannot
  trivially see the section tree from the response type alone — they
  must consult `available_sections` TOC (Phase 4) or
  `structure://rx-insert` resource (Phase 5).
- Per-field `present:true/false` would have given a uniform way to say
  "this drug structurally has §6.6 but it is empty". We accept that
  this granularity is overkill for most fields; for the
  trust-critical few we use `confirmed_absent` instead.

**Neutral**

- Citation format unchanged — `field_sections: {field_name → section_no}`
  remains the per-field provenance mechanism.
- `unmapped_sections` remains the future-TFDA-addition canary.
- `attribution` block continues to disclose third-party wrapper status.

## Verification

A future reader / agent can confirm this decision is in force by:

- The presence of flat field names like `geriatric`, `pregnancy`,
  `boxed_warning` in `_SECTION_NUMBERS` (or in the special-case
  extractor for `boxed_warning`) in `taiwan-fda-mcp/src/taiwan_fda_mcp/tools.py`.
- The **absence** of any class named `RxPackageInsert` /
  `RxPackageInsertContent` / `Section0X*` in
  `taiwan-fda-mcp/src/taiwan_fda_mcp/tool_responses.py`.
- `GetPackageInsertResponse` continuing to expose `fields: dict[str, str]`
  rather than a nested `insert: RxPackageInsert`.
- The schema snapshot
  `taiwan-fda-mcp/tests/snapshots/get_package_insert.output.json`
  staying within ~30 KB after Phase 3.1 lands (vs. an estimated 80 KB+
  if `$ref` dereferencing had inlined every section model).

Signals that would force a revisit:

- A future MCP client emerges that demonstrably handles nested response
  types better than flat dicts (advertises "deep-schema friendly"),
  AND token economics shift (e.g. context-budget-by-default goes from
  200 k to 2 M).
- TFDA fundamentally restructures the 仿單 format such that a flat
  field-name space cannot disambiguate sections.
- A peer healthcare MCP server ships a nested-typed response and the
  comparison is favourable in measured agent-task accuracy.

## References

- Survey of production healthcare MCP servers (no nesting precedent):
  [Cicatriiz/healthcare-mcp-public](https://github.com/Cicatriiz/healthcare-mcp-public),
  [openpharma-org/ema-mcp](https://github.com/openpharma-org/ema-mcp),
  [Augmented-Nature/OpenFDA-MCP-Server](https://github.com/Augmented-Nature/OpenFDA-MCP-Server),
  [JamesANZ/medical-mcp](https://github.com/JamesANZ/medical-mcp).
- Anthropic — [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents).
- Anthropic — [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp).
- FastMCP — [PR #2720: Fix root-level $ref in outputSchema](https://github.com/PrefectHQ/fastmcp/pull/2720).
- FastMCP — [Issue #1784: nested Pydantic unpacking](https://github.com/jlowin/fastmcp/issues/1784).
- FastMCP — [Tools documentation](https://gofastmcp.com/servers/tools).
- Triggering plan: `docs/superpowers/plans/2026-05-24-full-rx-otc-structure-coverage.md`
  (private — outer repo `/docs/*` gitignored).
- [ADR-0002](./0002-mandatory-rules-server-instructions.md) — directive
  server instructions, which `boxed_warning` MUST-quote rule extends.
