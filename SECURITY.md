# Security Policy

## Reporting a Vulnerability

If you discover a security issue in OpenTaiMed (or its `taiwan-fda-mcp` server),
please report it **privately** rather than opening a public issue.

**Contact**: soobahorn@gmail.com

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce (ideally a minimal proof-of-concept)
- Affected version(s) / commit SHA
- Your assessment of severity, if you have one

I aim to acknowledge reports within **5 business days** and to provide a
remediation timeline within **14 business days** of acknowledgement. This is a
volunteer single-maintainer project — please be patient.

## What Counts as a Vulnerability

In scope:

- **Prompt injection / indirect injection** attacks against the MCP server that
  cause it to leak secrets, bypass intended tool-call gating, or produce
  attacker-controlled output the LLM would treat as trusted
- **Citation forgery** — any path by which a tool response can return a
  `source_url` / `human_url` / `last_update_date` that does not correspond to
  the actual TFDA upstream response
- **Data integrity** — wrapper-side modification of clinical content (text in
  `fields`) that diverges from the TFDA XML byte-for-byte (beyond the
  documented HTML-entity-decode → plain-text normalisation)
- **Credential leakage** — any path that exposes `.env` contents, API keys,
  or filesystem paths outside the repo root through tool responses or logs
- **Denial of service** against a public deployment via crafted query input

Out of scope (please do not report):

- TFDA upstream content being wrong, outdated, or missing — that is an
  upstream data issue; raise it with the Taiwan FDA
- Rate limiting / abuse of the public `mcp.fda.gov.tw` and `data.fda.gov.tw`
  endpoints — those are owned by the TFDA, not by this project
- Recommendations like "use a different license" or "switch web framework" —
  these are roadmap discussions, not vulnerabilities

## Disclosure Timing

Once a fix is shipped, I will credit the reporter in the relevant `CHANGELOG.md`
entry unless they prefer to remain anonymous. Public disclosure of details
follows the fix release by at least 7 days.

## Clinical Safety Note

This project is a research and tooling effort. It is **NOT** a medical device,
**NOT** a clinical decision support system, and **NOT** endorsed by the Taiwan
FDA. Any vulnerability that allows the system to produce fabricated clinical
information (e.g. the LLM hallucinating drug data without calling a tool) is
treated as a high-severity issue — see e.g. the P0 hallucination fix (the
MANDATORY-RULES server instructions) in `taiwan-fda-mcp`.
