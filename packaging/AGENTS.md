# packaging — cross-platform release builds

## Purpose

Build scripts and artifacts for macOS (venvstacks layers + DMG), Linux (deb/rpm), and Windows (portable zip). Attached to GitHub Releases via `.github/workflows/release.yml`.

## Ownership

| Path | Role |
|------|------|
| `build.py` | venvstacks export, fingerprint |
| `_export/` | Python layer tree for Swift embed (generated) |
| `linux/` | deb/rpm staging |
| `windows/` | zip + winget metadata |
| `scripts/` | Shared packaging helpers (see table below) |
| `macos/` | Hardened-runtime entitlements for Developer ID sign |

**`packaging/scripts/` (macOS release path):**

| Script | Role |
|--------|------|
| `macos-app-install.sh` | User DMG upgrade/install (bundled in `.app`) |
| `mount-dmg.sh` | Mount release DMG; co-located with installer |
| `create-dmg.sh` | Stage → `dist/llm-swarm-router.dmg` |
| `codesign-mac-app.sh` | Developer ID + entitlements on Stage `.app` |
| `import-codesign-cert.sh` | Import `.p12` into CI keychain |
| `notarize-dmg.sh` | Submit DMG to Apple + staple |
| `maybe-notarize-dmg.sh` | Notarize when Apple secrets present (CI) |
| `local-notarized-dmg.sh` | Maintainer one-shot: build → sign → DMG → notarize |

Index: [README.md](README.md). macOS app bundle: [../apps/netllm-mac/Scripts/build.sh](../apps/netllm-mac/Scripts/build.sh). Signing setup: [../docs/macos-code-signing.md](../docs/macos-code-signing.md).

## Local Contracts

- macOS: `packaging/build.py --venvstacks-only` then `apps/netllm-mac/Scripts/build.sh release`
- **macOS distribution:** ad-hoc DMGs fail Gatekeeper on macOS 26+ (`no usable signature`); stable releases require Developer ID sign + notarize ([macos-code-signing.md](../docs/macos-code-signing.md)). CI: `release.yml` imports cert → `codesign-mac-app.sh` → `create-dmg.sh` → `maybe-notarize-dmg.sh` → SHA256
- `build.sh` uses ad-hoc sign locally unless `CODESIGN_IDENTITY` is set; maintainer gatekeeper-safe DMG: `packaging/scripts/local-notarized-dmg.sh`
- End-user DMG install/upgrade: bundled `macos-app-install.sh` + co-located `mount-dmg.sh`; repo `scripts/upgrade-mac-app.sh` wraps the same installer for maintainers only
- Release workflow renames DMG to `llm-swarm-router.dmg`
- Linux/Windows alpha artifacts include systemd/service install paths documented in platform docs
- Do not commit `_build/`, `_export/` churn unless intentional lock/export updates

## Work Guidance

- Bump all workspace package versions + `uv lock` before `gh release create`
- macOS notarized release: configure GitHub secrets per [macos-code-signing.md](../docs/macos-code-signing.md) before tagging; verify with `local-notarized-dmg.sh` locally when cert is in Keychain
- Maintainer local Apple credentials: gitignored repo-root `.env` (`set -a && source .env && set +a` before `local-notarized-dmg.sh`); never commit — `.env` is in `.gitignore`
- Packaging smoke: `./scripts/ci.sh packaging`
- macOS packaging PRs may touch both `packaging/` and `apps/netllm-mac/`

## Verification

```bash
./scripts/ci.sh packaging
uv run python packaging/build.py --print-fingerprint
apps/netllm-mac/Scripts/build.sh release   # macOS full path
```

Details: [../docs/ci-and-release.md](../docs/ci-and-release.md).

## Child DOX Index

| Path | Contract |
|------|----------|
| [`macos/`](macos/) | `entitlements.plist` for embedded Python (hardened runtime) |
| [`linux/`](linux/) | deb/rpm stage trees (generated) |
| [`windows/`](windows/) | zip and winget manifests |

Platform subfolders are build outputs and scripts; no nested AGENTS.md unless ownership splits by OS team.
