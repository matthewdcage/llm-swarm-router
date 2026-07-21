# Repo-wide lint index

Snapshot of every lint issue found across the git-tracked repository (i.e. what
`ruff check .` / `ruff format --check .` see once `.gitignore`d paths are
excluded — matches what CI actually checks out and lints). Only Python is
linted in this repo (`ruff`, configured in [pyproject.toml](../pyproject.toml)
`[tool.ruff]`); there is no SwiftLint, ESLint, or Biome config for the Swift
app or dashboard JS/HTML/CSS.

Generated: 2026-07-22, via `uv run ruff check .` and `uv run ruff format --check .`
from the repo root (both respect `.gitignore` by default).

Note: `tests/test_coordinator_dispatch.py` is `.gitignore`d (line 46) and not
part of the committed repo, so it's excluded here even though it has several
`E501` violations when linted directly by file path — those never reach CI.

## Findings

| # | File | Rule | Lines | Issue | Fix | Status |
|---|------|------|-------|-------|-----|--------|
| 1 | `scripts/generate-dashboard-tokens.py` | E501 | 14, 17 | `OUTPUT_CSS` path-join line and the `HEADER` docstring's first line exceed the project's 88-char limit (`[tool.ruff] line-length = 88`) | Wrap the path expression across lines; shorten the header comment | Fixed |
| 2 | `scripts/generate-dashboard-tokens.py` | format | whole file | `ruff format --check` reports the file would be reformatted (same two lines as #1, plus formatter's own line-wrapping) | `ruff format` | Fixed |

## Verification after fix

```bash
uv run ruff check .            # 0 errors
uv run ruff format --check .   # all files formatted
python3 scripts/generate-dashboard-tokens.py --check   # generated CSS output unchanged
./scripts/ci.sh lint           # unaffected: scripts/ was never in ci.sh's lint scope (packages/ tests/ only)
```

This script is a **generator**, not app code — its own formatting has no
runtime effect on the CSS it emits. `dashboard-tokens.css` (not itself
ruff-tracked) was diffed before/after: the only change is the header
*comment* text ("do not edit by hand." → "do not edit.", shortened to fit
the line-length fix inside the `HEADER` string literal) — zero change to
any CSS rule or custom property. Regenerated and committed alongside the
script fix so the checked-in artifact matches its generator's current
output (`--check` would otherwise fail on the header line).
