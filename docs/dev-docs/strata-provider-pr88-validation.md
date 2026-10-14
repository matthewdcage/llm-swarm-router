# Strata provider PR #88 — validation record

Public validation notes for co-developers (no host-specific secrets or paths).

## Branch

- `feat/strata-provider` → `main`
- Head commit: `1b2955477c55bc0a01dc7140f03660b7ef7d4f05`

## Scope (27 paths)

| Area | Paths |
|------|--------|
| Registry | `packages/netllm-core/local_providers.py`, `models.py`, generated `dashboard.js`, `config.example.toml` |
| Discovery | `packages/netllm-discovery/strata.py`, `local.py` |
| Agent | `service/backends.py`, `service/engine.py`, `static/pages/backends.js` |
| CLI | `packages/netllm-cli/commands/diagnose.py` |
| macOS | `apps/netllm-mac/.../SettingsViewModel.swift` |
| Docs | `docs/strata-integration.md`, `linux-install.md`, `editor-integration.md` |
| Tests | `test_strata_*.py`, `test_provider_fingerprint.py`, conformance + contract updates |

## Linux (maintainer workstation, Ubuntu)

| Command | Result |
|---------|--------|
| `./scripts/ci.sh lint` | Pass |
| `./scripts/ci.sh test` | **1950 passed**, 43 skipped (~114s) |

Focused Strata kits: `test_strata_provider`, `test_strata_autoload`, `test_provider_fingerprint`, `kit_local` (included in full run).

## GitHub Actions (PR #88)

| Job | Result |
|-----|--------|
| `lint` | Pass |
| `test (ubuntu-latest)` | Pass |
| `menubar-lifecycle` | Pass (Swift build + tests on `macos-14` runner) |
| `packaging-smoke` (Linux + Windows) | Pass |
| `sdk`, `types` | Pass |

`e2e` and `test (windows-latest)` may still be running on first push; re-check the PR checks tab.

## macOS hardware smoke (Mac mini, arm64)

Repo checkout on branch `feat/strata-provider`, `uv sync` OK.

| Command | Result |
|---------|--------|
| `uv run pytest tests/test_strata_provider.py tests/test_contract.py tests/conformance/kit_local.py` | **98 passed**, 16 skipped |
| `swift build` / `Scripts/build.sh release` | **Failed** on this host: Command Line Tools + macOS 27.x SDK errors in `WelcomeView.swift` / `SettingsWindowView.swift` (immutable `self` in `@ViewBuilder` helpers). **Not introduced by Strata** (`SettingsViewModel` only adds registry companions). |
| `./scripts/ci.sh lint` (partial) | Ruff/format pass; `check-engine-erosion.py` failed when invoked with system Python 3.9 (`ast.Import \| ast.ImportFrom`); use `uv run` / CI Python 3.12 |

**Authoritative macOS gate:** GitHub `menubar-lifecycle` (same `build.sh release` path as release docs).

## Operator notes

- Strata defaults in **Linux + Windows** discovery only; macOS enables via Settings → Network → Local providers.
- `:8080` shared with oMLX: fingerprint + doctor warn when both providers are enabled.
- `routing.strata_auto_load` (default `true`): `POST /load` before proxy when `engine_loaded` is false.

See [strata-integration.md](../strata-integration.md).
