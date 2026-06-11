# Contributing to llm-swarm-router

Thank you for helping make **llm-swarm-router** (netllm) a better mesh router for local LLM backends. This project is open source and community-driven, bug reports, docs fixes, platform support, and feature PRs are all welcome.

## Quick links

| Topic | Where |
|-------|-------|
| Bug or regression | [Open a bug report](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml) |
| Feature idea | [Open a feature request](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=feature_request.yml) |
| Security issue | [SECURITY.md](SECURITY.md): please do **not** open a public issue |
| Code of conduct | [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| Agent/AI context | [AGENTS.md](AGENTS.md) |
| Architecture | [AGENTS.md](AGENTS.md) |
| Platform matrix | [docs/platform-matrix.md](docs/platform-matrix.md) |

## Ways to contribute

You do not need to write code to help:

- **Reproduce and document bugs**: versions, OS, backends, config snippets, and `netllm doctor` output
- **Improve docs**: install guides, editor wiring, swarm setup, typos
- **Add tests**: especially for routing, discovery, and API contract behavior
- **Platform coverage**: Linux systemd, Windows service, macOS menubar, packaging
- **Review PRs**: constructive feedback on design and edge cases

## Development setup

**Requirements:** Python 3.11+, [uv](https://docs.astral.sh/uv/), git.

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync --frozen   # or uv sync after changing dependencies
./netllm init
./netllm serve          # agent on http://127.0.0.1:11400
```

CI and reproducible installs use the committed `uv.lock` (`uv sync --frozen`).

Use `./netllm` from the repo root during development, it works without a global install.

**Verify** (second terminal, while `serve` is running):

```bash
scripts/agent-verify-setup.sh
./netllm doctor
```

Optional global CLI: `./netllm install`

### macOS menubar app (optional)

Requires macOS 15+, Apple Silicon, Xcode CLT or full Xcode:

```bash
uv sync
apps/netllm-mac/Scripts/build.sh release
```

See [docs/macos-install.md](docs/macos-install.md) and [packaging/README.md](packaging/README.md).

### Linux / Windows packages (optional)

On Ubuntu x86_64: `NETLLM_VERSION=0.0.0-dev ./packaging/linux/build-deb.sh` (and `build-rpm.sh`).

On Windows: `.\packaging\windows\build-zip.ps1 -Version 0.0.0-dev`

See [packaging/README.md](packaging/README.md) and [docs/platform-matrix.md](docs/platform-matrix.md).

## Before you open a PR

1. **Search existing issues and PRs**, avoid duplicate work.
2. **Open an issue first** for large features or architectural changes so we can align on approach.
3. **Keep PRs focused**, one logical change per PR is easier to review and merge.
4. **Run the checks locally** (same as CI):

```bash
./scripts/verify-before-pr.sh
```

On macOS, that runs lint + test + `swift build -c release` for the menubar app. Add `--full` to run menubar e2e when `apps/netllm-mac/build/Stage/llm-swarm-router.app` exists.

`lint` (~1s), `test` (~20s), and `packaging` (deb/rpm on Linux, zip on Windows) can run separately: `./scripts/ci.sh lint`, `./scripts/ci.sh test`, or `./scripts/ci.sh packaging`.

**macOS menubar changes:** also read [docs/ci-and-release.md](docs/ci-and-release.md) — CI uses `macos-14` / Swift 5.10; Tahoe-only APIs must be SDK-gated.

Full CI (lint → test + packaging-smoke + menubar-lifecycle on macOS): see [docs/ci-and-release.md](docs/ci-and-release.md).

5. **Add or update tests** when you change behavior, avoid trivial assertions; cover real paths.
   - Routing, swarm, or discovery changes should keep the **two-agent acceptance harness** green: `uv run pytest tests/test_e2e_two_agents.py -q` spins up two real agents + mock providers over HTTP and asserts combined catalogs, load spreading, and loop-guarded agent hops. Extend it when you add mesh behavior.
6. **Update docs** when user-facing behavior, CLI flags, or install steps change. Skills live under `.agents/skills/` — run `scripts/sync-agent-skills.sh` after editing.

### Optional: pre-commit hooks

Fast feedback on **staged files only** (~3s): ruff lint/format fixes plus whitespace, YAML, and secret checks.

```bash
uv sync
uv run pre-commit install
uv run pre-commit run --all-files   # optional dry run before pushing
```

Pre-commit includes `ruff-format`; CI enforces the same via `./scripts/ci.sh lint`.

## Pull request workflow

1. Fork the repository and create a branch from `main`:

```bash
git checkout -b feat/short-description
# or: fix/, docs/, chore/
```

2. Make your changes and commit with [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(discovery): add custom vLLM port scan
fix(cli): surface mDNS errors on Linux
docs: clarify swarm.cluster_token for LAN mode
test(agent): cover Anthropic streaming bridge
```

Focus commit messages on **why**, not only what changed.

3. Push and open a PR against `main`. Fill out the PR template completely.

4. CI runs lint on Ubuntu, then tests on Ubuntu and Windows (`./scripts/ci.sh`). Fix failures before requesting review.

5. Maintainers may ask for changes or suggest splitting large PRs, that is normal.

### PR size guidance

| Size | Guidance |
|------|----------|
| Small (&lt; ~200 lines) | Docs, focused bugfix, single-package change: fastest path to merge |
| Medium | New CLI command, discovery tweak, test expansion: include test plan in PR |
| Large | New platform surface, routing strategy, packaging pipeline: issue discussion first |

## Code style

This repo uses consistent Python tooling:

| Tool | Config |
|------|--------|
| Formatter / linter | [ruff](https://docs.astral.sh/ruff/): line length 88, rules `E`, `F`, `I`, `UP` |
| Type checking | basedpyright, mode `standard`, Python 3.11 |
| Tests | pytest, asyncio mode `auto` |

**Conventions:**

- Match existing package layout under `packages/`
- Typer + Rich patterns for CLI changes in `netllm-cli`
- Vendor SDKs stay isolated: `netllm-sdk-openai` and `netllm-sdk-anthropic` only: `netllm-core` must not import `openai` or `anthropic`
- Do not delete files: move to local `archived/` and log in `archived/ARCHIVE_LOG.txt` (gitignored; never commit)
- Do not commit secrets, API keys, or `.cursor/mcp.json`

### Agent skills

Canonical skills live in `.agents/skills/`. After editing a skill, sync copies:

```bash
scripts/sync-agent-skills.sh
```

Targets: `.claude/skills/`, `.cursor/skills/`, `.github/skills/`

### SDK version bumps

Tracked in [docs/sdk-versions.md](docs/sdk-versions.md). One package per PR. Checklist:

1. Edit `anthropic>=…` or `openai>=…` in `packages/netllm-sdk-*/pyproject.toml`
2. `uv sync` and commit `uv.lock` (CI uses `uv sync --frozen`)
3. Update `docs/sdk-versions.md` (resolved version + date)
4. `./scripts/ci.sh sdk` then `./scripts/ci.sh`
5. Read upstream SDK changelog; adjust the layer documented in `docs/sdk-versions.md` (adapter, bridge, or agent)

Dependabot opens weekly PRs with the `sdk-bump` label for `packages/netllm-sdk-*/`. A weekly [sdk-canary workflow](.github/workflows/sdk-canary.yml) tests latest upstream SDKs and opens a `sdk-canary` issue on failure.

## Testing tips

```bash
# Full suite
uv run pytest tests/ -v

# Anthropic bridge
uv run pytest packages/netllm-sdk-anthropic/tests/ tests/test_anthropic_bridge.py -v

# Agent HTTP surface
uv run pytest tests/test_agent.py -v

# Lint only (same as CI lint job)
./scripts/ci.sh lint
```

When adding discovery or routing behavior, include tests that do not require real GPU backends (use mocks/fixtures like existing tests).

## Platform contributions

| Platform | Primary paths | Doc |
|----------|---------------|-----|
| macOS menubar | `apps/netllm-mac/` | [docs/macos-install.md](docs/macos-install.md) |
| Linux packages | `packaging/linux/` | [docs/linux-install.md](docs/linux-install.md) |
| Windows packages | `packaging/windows/` | [docs/windows-install.md](docs/windows-install.md) |
| Core agent | `packages/netllm-agent/` | [AGENTS.md](AGENTS.md) |

Cross-platform changes should preserve the shared agent contract on `:11400`, see [docs/platform-matrix.md](docs/platform-matrix.md).

## Graphify (optional, for architecture work)

This repo maintains an AST knowledge graph. After modifying code:

```bash
graphify update .
```

See `graphify-out/GRAPH_REPORT.md` for community structure. Do not commit generated graph output unless a maintainer asks.

## Recognition

Contributors are credited through git history and release notes. The project is licensed under [MIT](LICENSE), copyright is held by **netllm contributors**.

## Questions?

- **Usage / setup:** open a [bug report](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml) with the `question` label context, or check [docs/editor-integration.md](docs/editor-integration.md)
- **Design discussion:** open a [feature request](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=feature_request.yml) before large PRs

We appreciate every contribution, from a one-line doc fix to a new routing strategy.
