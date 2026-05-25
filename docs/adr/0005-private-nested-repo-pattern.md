# ADR-0005: `.private/` as a nested independent git repository

- **Status**: Accepted
- **Date**: 2026-05-25

## Context

AI-assisted development produces a set of artefacts that do not belong in
a public repository but *do* benefit from version control:

- `STATE.md` — current-session decisions, in-progress items, blockers.
- `TODO.md` — durable cross-session personal backlog (until / unless
  migrated to GitHub Issues).
- `HANDOFF.md` — the bridge between sessions; what to read first on
  resume, what the previous session left half-done.
- Execution plans (`docs/plans/`) — scratch reasoning behind a multi-step
  change. Their *output* (commits, CHANGELOG entries) is the public
  artefact; the plans themselves are working memory.
- Source-material exploration (PDFs, xlsx schema dumps, API capture
  files) — large, possibly copyrighted, definitely not part of the
  published codebase.

Four storage options were considered:

| Option | Versioned? | Coupled to code? | Private? | Notes |
|---|---|---|---|---|
| Commit to public repo | yes | yes | no | Leaks internal state; pollutes public history with churn. |
| Commit to a *separate* private GitHub repo | yes | no | yes | Loses file-system proximity; agents would have to know to look elsewhere. |
| Single `.gitignore` entry + plain files on disk | no | yes | yes | Loses version history — biggest cost. |
| Nested independent git repo inside the working tree | yes | yes | yes | Two git operations per session-end, but each is local and fast. |

A prior project (`nhi-knowledge-extractor`) used the nested-repo pattern
successfully through its own public-push prep — sufficient real-world
validation to adopt the same shape here.

## Decision

Create `.private/` as a directory in the working tree of OpenTaiMed.
Initialise an independent git repository inside it (`.private/.git/`).
Add `/.private/` to the outer repo's `.gitignore` so the outer repo
never tracks the directory or its contents.

The standard layout inside `.private/`:

```
.private/
├── .git/                    own history, no remote by default
├── .gitignore               OS / editor noise only
├── README.md                explains the dual-repo arrangement
├── HANDOFF.md               session-to-session bridge (kept current)
└── docs/
    ├── STATE.md             current decisions
    ├── TODO.md              durable backlog
    ├── plans/               execution scratch
    └── source-materials/    PDF / xlsx / API captures
```

The agent's standard ritual:

1. **Session start** — read `.private/HANDOFF.md` first.
2. **Session end** — overwrite `.private/HANDOFF.md` with the new "what's
   next" snapshot. Commit it (and any `docs/STATE.md` / `TODO.md` edits)
   inside `.private/`.

The outer repo's commits remain code-and-public-docs only.

## Consequences

**Positive**
- Full git history for private state, with no leakage risk from the
  outer repo (the outer repo cannot accidentally `git add` anything
  under `.private/` because git treats it as a submodule-shaped opaque
  blob and `.gitignore` makes it invisible).
- File-system proximity means an agent reading `~/Projects/opentaimed/`
  can read `.private/HANDOFF.md` in the same pass as the code, without
  any cross-repo plumbing.
- The pattern is opt-in: a forker of the public repo simply doesn't get
  `.private/`. There is no "delete this before publishing" step.

**Negative / accepted trade-offs**
- Two git operations per session: one inside `.private/`, one in the
  outer repo. Mitigated by making the inside-`.private` commits small
  and routine (HANDOFF.md update is a one-line ritual).
- `.private/` has **no off-machine backup by default**. If the developer's
  laptop fails, session history is lost. Mitigation: add a private
  GitHub remote to `.private/` if backup matters. The outer repo's
  `origin` is *not* a substitute — pushing the outer to GitHub does not
  push `.private/`.
- Tools that scan from the working-tree root (`grep -r`, IDE search) by
  default see `.private/` content. Sometimes useful (agent can grep
  for past decisions); sometimes noisy (a `find` over the repo includes
  scratch files). Adjust on a per-tool basis (`.git/info/exclude`,
  ripgrep `--ignore-file`, etc.).

**Neutral**
- Some materials in `.private/docs/` are eventually promoted to public
  `docs/` (e.g. cleaned-up data-source analysis becomes
  `docs/data-sources.md`). The nested-repo pattern doesn't change that
  workflow — it's still "polish, then `git add` in the outer repo".

## Verification

- `git status` in the outer repo never shows anything under `.private/`.
  If it does, the `.gitignore` rule is broken.
- `git -C .private/ log` accumulates a commit per session end (target
  cadence; not enforced).
- `find . -name STATE.md` from the outer repo root surfaces only
  `.private/docs/STATE.md`. No copies leak to the outer tree.

Revisit when: (a) the developer adopts a different note-taking tool
that already provides version history (e.g. Obsidian with git sync) —
then `.private/` may be redundant; (b) team grows past one person —
shared session memory needs different infrastructure (Linear, Notion,
shared private repo).

## References

- Commit `16a1504` — outer `.gitignore` formalises `/.private/` exclusion.
- `.private/README.md` — in-tree explanation of the layout for any
  future reader of the working directory.
- `nhi-knowledge-extractor` commit `162a0b4` — prior adoption of the
  same pattern, by the same maintainer.
