# CI, macOS build, and release

Canonical checklist for contributors and coding agents. Mirrors what GitHub Actions runs on every PR and release.

## PR CI (always)

| Job | Runner | Command equivalent |
|-----|--------|-------------------|
| lint | `ubuntu-latest` | `./scripts/ci.sh lint` |
| test | `ubuntu-latest`, `windows-latest` | `./scripts/ci.sh test` |
| packaging-smoke | Ubuntu + Windows | `./scripts/ci.sh packaging` |
| sdk | `ubuntu-latest` | `./scripts/ci.sh sdk` |
| menubar-lifecycle | `macos-14` | `apps/netllm-mac/Scripts/build.sh release` then `scripts/test-menubar-e2e.sh` + `scripts/test-menubar-lifecycle.sh` |

**Before push:** from repo root:

```bash
./scripts/verify-before-pr.sh          # Python CI + optional macOS Swift
./scripts/verify-before-pr.sh --full   # + menubar e2e when .app exists
```

Rebase on `origin/main` before opening a PR; branch protection requires an up-to-date head.

## macOS menubar build constraints (lessons from v0.3.0.0)

CI uses **`macos-14`** with **Swift 5.10** and the **macOS 15 SDK**. Local Xcode 16+ / Swift 6 builds can hide failures until CI.

| Rule | Why |
|------|-----|
| `apps/netllm-mac/Package.swift` → `swift-tools-version: 5.9` | Swift 6 tools fail on `macos-14` runners |
| `platforms: [.macOS(.v14)]` unless runners bump to `macos-15`+ | `macOS(.v15)` needs PackageDescription 6.0 |
| Menubar SwiftUI views that call `MenubarAppModel` → `@MainActor` on the view; `@MainActor` on action closures passed to helpers | Swift 5.10 strict concurrency in release builds |
| Liquid Glass `glassEffect` → `#if LIQUID_GLASS_SDK` only | API exists in **macOS 26 SDK**; CI SDK has no symbol. `build.sh` sets `-DLIQUID_GLASS_SDK` when `xcrun --sdk macosx --show-sdk-version` major ≥ 26 |
| `build.sh` + `set -u`: branch on `${#SWIFT_FLAGS[@]}` before expanding empty arrays | Empty `"${SWIFT_FLAGS[@]}"` aborts under `set -u` on bash 3.2 |

**Local macOS verify after Swift changes:**

```bash
cd apps/netllm-mac && swift build -c release
apps/netllm-mac/Scripts/build.sh release --no-rebuild-donor   # if _export/ warm
bash scripts/test-menubar-e2e.sh                              # needs Stage .app
```

Design tokens: after editing `apps/netllm-mac/design-tokens.json`, run `scripts/generate-dashboard-tokens.py` (CI lint checks `--check`).

## Agent smoke (functional, not CI)

While `./netllm serve` runs:

```bash
curl -sf http://127.0.0.1:11400/health
./netllm status
./netllm models
./netllm test
./netllm test --api anthropic --model <chat-model>   # not TTS/embedding ids
```

Use **`./netllm`** from repo root — not the global `netllm` on PATH (`scripts/agent-verify-setup.sh` prefers global when present).

`./netllm doctor` may report “port in use” while `serve` is running; that is expected.

## Release workflow

Triggered by publishing a GitHub Release whose tag matches `pyproject.toml` (e.g. `v0.3.0.0`).

1. Merge feature work to `main`.
2. Bump **all** workspace `version =` in `**/pyproject.toml` + `packages/netllm-core/src/netllm_core/version.py` `_FALLBACK_VERSION`.
3. `uv lock` and commit.
4. Add `docs/release-notes/vX.Y.Z.md`; link from `docs/release-notes/v0.2.3.md` chain if applicable.
5. `git push origin main`
6. `gh release create vX.Y.Z --title "..." --notes-file docs/release-notes/vX.Y.Z.md`

**macOS upgrade text in release notes:** on macOS 26+, lead with **build from source + `macos-app-install.sh --source`** ([macos-install.md](macos-install.md)); menubar **Updates** and `--dmg` paths when notarized. Do not assume drag-to-Applications works. See [v0.3.0.2 notes](release-notes/v0.3.0.2.md).

Release job builds and attaches: `llm-swarm-router.dmg` (ad-hoc until notarized), `.deb`, `.rpm`, Windows zip, `netllm.yaml`, SHA256 sidecars.

**macOS Gatekeeper:** configure Developer ID + notarization secrets per [macos-code-signing.md](macos-code-signing.md). Release workflow signs after menubar tests, notarizes the DMG, then writes SHA256 sidecars. Without secrets, DMGs remain ad-hoc signed — point users at [macos-troubleshooting.md](macos-troubleshooting.md#gatekeeper-blocks-install-or-launch).

Watch: `gh run list --workflow=release.yml --limit 1` then `gh run view <id>`.

## Do not

- Commit `test-menubar-e2e.sh` steps that call scripts not in the repo (e.g. `test_macos_app_install_flags.sh` must be committed with the e2e hook-up).
- Set `Package.swift` to Swift 6 / `macOS(.v15)` without bumping `.github/workflows/ci.yml` `menubar-lifecycle` to `macos-15`+.
- Reference Tahoe-only SwiftUI APIs without `LIQUID_GLASS_SDK` or an SDK-gated file.
