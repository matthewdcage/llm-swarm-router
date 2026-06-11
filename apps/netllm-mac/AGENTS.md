# netllm-mac — macOS menubar app

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Swift menubar application that supervises the netllm Python agent, exposes settings/welcome/updater UI, and embeds venvstacks Python layers from packaging export.

## Ownership

| Path | Role |
|------|------|
| `Sources/App/` | Entry, delegate, lifecycle |
| `Sources/Menubar/` | Status item, stats polling |
| `Sources/Server/` | Process supervisor, control socket |
| `Sources/Config/` | TOML slices, CLI shim, `AgentAPI` HTTP client, branding, tokens |
| `Sources/AppView/` | Settings (`SettingsViewModel` live poll), welcome, about, glass chrome |
| `Sources/Updater/` | GitHub Releases check, in-app install |
| `Sources/Welcome/` | First-run wizard |
| `Scripts/build.sh` | Release/stage build (venvstacks + Swift); ad-hoc sign unless `CODESIGN_IDENTITY` set |
| `design-tokens.json` | Dashboard token source (sync via `scripts/generate-dashboard-tokens.py`) |

## Local Contracts

- `Package.swift`: swift-tools **5.9** (CI runs Swift 5.10 on macos-14)
- SwiftUI views: `@MainActor`; gate Tahoe `glassEffect` behind `LIQUID_GLASS_SDK` in `build.sh`
- In-app update must stop agent and free `:11400` — no manual **Stop** required first
- Repo checkout does not update `/Applications/llm-swarm-router.app`; user upgrade: menubar **Updates** or bundled `macos-app-install.sh` (embedded under `Contents/Resources/Scripts/`); `scripts/upgrade-mac-app.sh` is repo-maintainer wrapper only
- Logs: `~/Library/Application Support/netllm/logs/`
- **Gatekeeper:** ad-hoc Stage/DMG builds do not launch on macOS 26+; release path is Developer ID + notarize via [packaging/scripts/local-notarized-dmg.sh](../../packaging/scripts/local-notarized-dmg.sh) or CI ([macos-code-signing.md](../../docs/macos-code-signing.md))
- **Settings live status:** `SettingsViewModel` polls `/health` + `/netllm/v1/status` every 2s while Settings is open; **Restart Agent** waits for `/health` before refreshing stats (avoids stale "waiting for HTTP health" / backends `—`)
- **LAN swarm QoL:** welcome **Listen on LAN** sets `swarm.subnet_scan = true`; agent probes LAN at startup when enabled. Settings auto-runs `POST /netllm/v1/admin/peers-scan` once per session when agent is healthy (display only; runtime merge is agent-side). Manual **Scan & save** still persists `swarm.peers` when mDNS is blocked.
- **HTTP client host:** Settings and menubar **Open Dashboard** use `127.0.0.1` (`AppConfig.connectableHost`); web UI opened at `http://<LAN-IP>:11400/ui/` on the same Mac is equivalent after agent admin-host fix ([netllm-agent/AGENTS.md](../../packages/netllm-agent/AGENTS.md))

## Work Guidance

- Build: `uv sync`, `uv pip install venvstacks`, `apps/netllm-mac/Scripts/build.sh release`
- Validate updater/install with `tests/test_bundled_install_scripts.sh` before release tags
- Commit macOS install/update fixes as focused slices separate from unrelated work

## Verification

```bash
apps/netllm-mac/Scripts/build.sh release
scripts/verify-before-pr.sh
scripts/test-menubar-e2e.sh
tests/test_bundled_install_scripts.sh
```

User docs: [../../docs/macos-install.md](../../docs/macos-install.md), [../../docs/macos-troubleshooting.md](../../docs/macos-troubleshooting.md).

## Child DOX Index

None — Swift sources grouped under `Sources/` by concern; no nested AGENTS.md until a subtree gains independent release or ownership.
