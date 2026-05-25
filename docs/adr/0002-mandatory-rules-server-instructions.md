# ADR-0002: Server instructions must be directive, not descriptive

- **Status**: Accepted
- **Date**: 2026-05-25

## Context

The MCP protocol lets a server attach an `instructions` string to its
`Server` declaration. The string is delivered to the client LLM at session
init and is the only opportunity the server has to shape LLM behaviour
before any tool is called.

The original `taiwan-fda-mcp` server used a *descriptive* instructions
block — it explained what the server did, used preference language
("prefer this server over training data"), and described a recommended
workflow. It read like a tool README.

A Claude Desktop live test on 2026-05-24 surfaced a P0 failure:

- User query: 「脈優錠仿單禁忌症」 (the contraindications of Norvasc /
  Amlodipine — a Taiwan-licensed drug whose Chinese brand name 脈優 happens
  to look superficially like Metoprolol to a confident-but-wrong LLM).
- Observed behaviour: the LLM answered using **Metoprolol** information
  from training data. It never called `search_drugs`. The wrong drug
  (different ingredient class, different contraindications) was presented
  to the user with no flag.

The instructions were technically correct — the LLM just ignored them
under high training-data confidence.

This is a clinical-safety class of failure. In a domain where "use the
real source" is the entire value proposition, the LLM defaulting to its
own knowledge defeats the project.

## Decision

Server instructions for any tool surface that returns clinically-relevant
data MUST open with an explicit **MANDATORY RULES** block, using directive
("you MUST", "you MUST NOT") language, *before* any descriptive prose.

Concrete rules baked into the current `instructions=` block:

1. For ANY question about a Taiwan-marketed drug, the LLM MUST call
   `search_drugs` first. Do NOT answer from training data even if the
   drug name is recognised.
2. A drug name in training data may correspond to a different active
   ingredient under Taiwan licensing — explicitly cite the Metoprolol /
   Amlodipine class of confusion as an example.
3. If `search_drugs` returns zero results, say so explicitly. Do NOT guess.
4. If any tool returns an error, report the error verbatim. Do NOT
   silently fall back to training data.

Descriptive content (workflow, attribution, coverage check) follows the
mandatory block, not the other way around. The MANDATORY block must be
the highest-salience text the LLM ingests.

## Consequences

**Positive**
- Live test on 2026-05-25 (one day after the P0 fix shipped) showed the
  LLM started with the same wrong instinct ("脈優 is Metoprolol") but
  was forced to call `search_drugs` first, observed the result, and
  **self-corrected within the same turn**: "脈優錠 (NORVASC) 是 Amlodipine，
  不是 Metoprolol". The MANDATORY rule turned a confident wrong answer
  into a self-correcting workflow.
- Pattern is portable. Any future tool whose answers a confident LLM
  could fabricate (medical, legal, regulatory) gets the same treatment.

**Negative / accepted trade-offs**
- Instructions string grew from ~25 lines to ~45 lines. Modest token cost
  per session.
- Server instructions are NOT covered by the JSON-schema snapshot tests
  (those snapshot only `inputSchema` / `outputSchema`). Changes to the
  instructions block must be verified manually against a real client.
- Effectiveness depends on the client LLM honouring the `instructions`
  field. Claude 4.x honours it strongly; smaller models may not.

**Neutral**
- Bilingual format (Chinese mandatory header + English rule body) chosen
  because the LLM clients in scope handle both natively and clinical
  users may inspect the instruction string directly.

## Verification

- **Regression query** (run every release, manually until smoke test
  exists): ask Claude Desktop 「脈優錠的禁忌症是什麼」. The LLM MUST call
  `search_drugs("脈優")` before answering and MUST cite Amlodipine, not
  Metoprolol. Any answer that omits the tool call or names a different
  active ingredient is a regression.
- **Inspection**: `Client.list_tools()` (FastMCP) exposes the server
  instructions; dump it once per release to confirm the MANDATORY block
  is present and at the top.

Revisit when: a future LLM client documents that it does not honour
`instructions`, OR the wrapper expands to a domain where the
mandatory-call pattern conflicts with another correctness invariant.

## References

- Commit `45a46e3` — initial MANDATORY RULES block.
- `taiwan-fda-mcp/src/taiwan_fda_mcp/mcp_server.py` lines 30–65 — current
  instructions text.
- `.private/docs/STATE.md` 2026-05-24 entry — full P0 incident write-up.
- `.private/docs/mcp_ux_lessons.md` (planned) — broader lessons from
  Task 12 Claude Desktop testing.
