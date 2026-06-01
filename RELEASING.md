# Releasing `taiwan-fda-mcp`

How to cut a public release to PyPI. The package is published from
`taiwan-fda-mcp/`; releases are driven by a git tag and finished by
GitHub Actions — you never run `uv publish` by hand for production.

## How publishing works

Pushing a `v*` tag triggers `.github/workflows/publish.yml`, which builds
the sdist + wheel and uploads to PyPI via **Trusted Publishing (OIDC)** —
no API token is stored anywhere. The `pypi` GitHub environment gates the
upload with a manual approval.

```
git tag vX.Y.Z  →  push tag  →  Actions builds  →  you Approve  →  PyPI
```

`git` commands work from anywhere inside the repo. The build runs in CI
inside `taiwan-fda-mcp/` automatically (the workflow sets the working dir).

## One-time setup (already done; here for reference)

1. **PyPI account** with 2FA enabled (and a separate TestPyPI account).
2. **Trusted Publisher** registered on PyPI for project `taiwan-fda-mcp`:
   owner `shin13`, repo `opentaimed`, workflow `publish.yml`,
   environment `pypi`. (Same on TestPyPI for rehearsals.)
3. **GitHub `pypi` environment** (repo Settings → Environments) with the
   maintainer as a required reviewer.

## Versioning (SemVer, 0.x)

- Bug fix → patch (`0.2.0` → `0.2.1`).
- New feature or **any breaking change** while on `0.x` → minor
  (`0.2.x` → `0.3.0`).
- A published version is **immutable** — PyPI rejects re-uploading the same
  version. If a release is broken, bump and release again; never try to
  overwrite.

## Release steps

### 1. Prepare a release PR

In one PR (run the verification gate before pushing):

- [ ] `taiwan-fda-mcp/pyproject.toml`: bump `version`.
- [ ] `uv lock` (in `taiwan-fda-mcp/`) so `uv.lock` matches the new version —
      otherwise CI `uv sync --frozen` fails.
- [ ] `CHANGELOG.md`: promote `[Unreleased]` → `[X.Y.Z] — <date>`; add a
      fresh empty `[Unreleased]`; add/refresh the footer compare links.
- [ ] Update the shipped-version references: `README.md` and `CLAUDE.md`
      (`shipped — vX.Y.Z`).
- [ ] Gate (from `taiwan-fda-mcp/`):
      `uv run ruff check . && uv run pyright src && uv run pytest`

Merge the PR (PR-only flow — CI must be green).

### 2. (Recommended for risky releases) Rehearse on TestPyPI

```bash
cd taiwan-fda-mcp
uv build
uv publish --publish-url https://test.pypi.org/legacy/ dist/*   # username __token__
# Then install the SAME way a user will (see the lesson below):
uvx --refresh --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ taiwan-fda-mcp
```

### 3. Tag and push

```bash
git checkout main && git pull --ff-only
git tag vX.Y.Z
git push origin vX.Y.Z
```

### 4. Approve the deployment

GitHub → Actions → the `publish` run is waiting on the `pypi` environment →
**Approve and deploy**. It builds and uploads (~1–2 min).

### 5. Verify — with the *literal* documented command

```bash
# Confirm the version is live
curl -s https://pypi.org/pypi/taiwan-fda-mcp/json | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"

# Run EXACTLY what the README tells users to run — no extra flags
uvx --refresh taiwan-fda-mcp
```

Success = the FastMCP banner + `Starting MCP server ... transport 'stdio'`
with no error; it then waits for a client (Ctrl+C to exit).

## Rollback (before you Approve)

```bash
# Cancel the waiting Actions run, then:
git push origin :vX.Y.Z   # delete remote tag
git tag -d vX.Y.Z         # delete local tag
```
Nothing reaches PyPI until you Approve.

## Lessons baked into this checklist

- **Verify with the command the docs actually show — byte for byte.**
  0.2.0 shipped with `uvx taiwan-fda-mcp-server` in every doc, which fails
  (`uvx` resolves a bare command to a package of the *same* name; the
  package is `taiwan-fda-mcp`). The TestPyPI rehearsal passed only because
  it used the `--from` form the docs never showed. Always smoke-test the
  exact published instruction. `tests/unit/test_packaging.py` now guards the
  console-script names as a regression backstop.
- **A rehearsal that differs from the real user path proves nothing.** Use
  the same install command on TestPyPI that the README gives users.
