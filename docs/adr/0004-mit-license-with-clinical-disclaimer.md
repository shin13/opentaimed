# ADR-0004: MIT License with a separate informational clinical disclaimer

- **Status**: Accepted
- **Date**: 2026-05-25

## Context

OpenTaiMed surfaces clinical data (Taiwan FDA package inserts) through a
machine-readable wrapper. Two licensing constraints interact:

1. **Open-source intent.** The project is intended to be freely copyable,
   forkable, and embeddable into other clinical or research tooling
   without negotiating a custom licence. This argues for a maximally
   permissive license.
2. **Clinical-safety liability.** Drug-information software in a clinical
   context can cause harm if relied upon as authoritative. The wrapper
   author has no medical license, no QMS, no MDR / FDA clearance, and
   absolutely no intent to be classed as a medical-device manufacturer.
   This argues for explicit, prominent disavowal of clinical responsibility.

Three license candidates were evaluated:

| License | Permissiveness | Patent grant | Recognition | Fits the use case? |
|---|---|---|---|---|
| MIT | Very high | No (silent) | Highest in modern OSS | Yes — minimal terms, max reuse |
| Apache-2.0 | High | Explicit | High, especially in enterprise | Heavier; patent grant unnecessary (no patentable IP in this wrapper) |
| GPL-3.0 | Copyleft | Explicit | Polarising | Wrong — would discourage hospital / vendor adoption |

A bare MIT license, however, says nothing about "this is not a medical
device" — and embedding that statement inside the MIT grant itself would
muddy the legal terms of the grant.

## Decision

Use **MIT** as the operative license. Append an **informational
disclaimer** as a labelled section *after* the standard MIT text in the
`LICENSE` file, with explicit framing that it is **not part of the MIT
grant** but is included for end-user clarity:

```
ADDITIONAL DISCLAIMER (informational; not part of the MIT grant):

This software is an INDEPENDENT, OPEN-SOURCE WRAPPER around public Taiwan
FDA (TFDA) APIs. It is NOT a product of, endorsed by, or affiliated with
the Taiwan FDA or any government agency. ... This software does not
provide medical advice. ...
```

Restate the disclaimer briefly and prominently in:

- repo-root `README.md` — `> [!IMPORTANT]` callout at the top.
- `taiwan-fda-mcp/README.md` — same callout, before any setup instructions.
- `SECURITY.md` — under "Clinical Safety Note".
- Every `get_package_insert` tool response — `attribution` Pydantic field
  carrying `wrapper="taiwan-fda-mcp (independent open-source project, NOT a TFDA product)"`.

## Consequences

**Positive**
- Maximum permissiveness for downstream users — copy, fork, integrate,
  commercialise, no copyleft contamination.
- Highest license recognition — MIT is the modal open-source license; no
  enterprise legal team will block adoption over license review.
- The clinical disclaimer is repeated in four places (LICENSE, two
  READMEs, every tool response) so it survives any single layer being
  stripped by a downstream packager.
- Separation of MIT grant from disclaimer keeps the grant legally clean:
  a court interpreting the MIT terms is not parsing medical-disclaimer
  prose mixed into the warranty section.

**Negative / accepted trade-offs**
- MIT provides no explicit patent grant. Irrelevant for this project (no
  patentable IP), but if downstream commercial users start asking for
  patent indemnification, the project may need to relicense under
  Apache-2.0. Compatible direction (MIT → Apache-2.0 is a permissible
  re-license path; reverse is not).
- The informational disclaimer is not legally binding in the way a
  warranty disclaimer baked into the license is. The MIT warranty
  disclaimer ("AS IS … WITHOUT WARRANTY OF ANY KIND") does the heavy
  legal lifting; the appended clinical text is communication, not law.
- A user is still capable of building a clinical-decision-support
  system on top of this wrapper without informing their users of its
  provenance. That is fundamentally not solvable at the license layer.

**Neutral**
- Copyright holder is `shin13` (matching the GitHub handle + the
  `authors` block in `taiwan-fda-mcp/pyproject.toml`). The decision to
  use a handle rather than a legal name is intentional — the project is
  released personally, not on behalf of any institution.

## Verification

- `LICENSE` file at repo root contains MIT text *and* the labelled
  additional disclaimer section.
- `README.md`, `taiwan-fda-mcp/README.md`, and `SECURITY.md` all carry
  a prominently-formatted non-official-wrapper callout.
- Every `get_package_insert` response includes an `attribution` block
  with the wrapper-disavowal string (snapshot-frozen via
  `tests/snapshots/get_package_insert.output.json`).

Revisit when: (a) commercial vendors ask about patent indemnity (consider
Apache-2.0); (b) the project is moved under an institutional umbrella
(may require relicensing or copyright assignment); (c) Taiwan or other
jurisdiction regulators publish guidance on classification of LLM-facing
drug-information wrappers.

## References

- `LICENSE` — full text including the appended disclaimer.
- `taiwan-fda-mcp/src/taiwan_fda_mcp/tools.py` — `_ATTRIBUTION` constant
  applied to every successful `get_package_insert` response.
- Commit `8e283e6` — adopted the MIT + disclaimer combination.
