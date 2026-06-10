# macOS install: netllm menubar app

Native menubar wrapper for **llm-swarm-router** (terminal CLI: `netllm`). The app supervises the
existing Python agent on port **11400**, it does not replace oMLX inference on port 8080.

**Troubleshooting:** [macos-troubleshooting.md](macos-troubleshooting.md) · **All platforms:** [platform-matrix.md](platform-matrix.md)

> **GitHub DMG status (2026-06):** Release DMGs are **ad-hoc signed** until Apple Developer notarization is enabled. On **macOS 26 (Tahoe)+**, Gatekeeper blocks ad-hoc menubar apps (`no usable signature`). Until notarized DMGs ship, use **build from source + install script** below (recommended) or **CLI-only** `./netllm serve`.

## Install channels

### Build from source + install script (recommended on macOS 26+)

Works on Tahoe without Gatekeeper blocks. Uses the same installer CI validates before release.

**Requirements:** macOS 15+, Apple Silicon, [uv](https://docs.astral.sh/uv/), git, Xcode command-line tools (`xcode-select --install`).

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
git checkout v0.3.0.2   # or latest tag from GitHub Releases

uv sync
uv pip install venvstacks
apps/netllm-mac/Scripts/build.sh release

packaging/scripts/macos-app-install.sh \
  --source apps/netllm-mac/build/Stage/llm-swarm-router.app
```

The installer quits any running menubar app, stops orphaned agents on `:11400`, copies to `/Applications/llm-swarm-router.app`, relaunches, and verifies `GET /ui/` returns HTTP 200.

**Upgrade:** pull/checkout the new tag, rebuild, run the same `macos-app-install.sh --source …` command.

**Verify:**

```bash
defaults read /Applications/llm-swarm-router.app/Contents/Info CFBundleShortVersionString
curl -sf http://127.0.0.1:11400/health
```

The app installs a CLI shim at `~/.config/netllm/bin/netllm` for terminal control.

### CLI only (no menubar, no Gatekeeper)

Agent + web dashboard without the Swift menubar app:

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
git checkout v0.3.0.2   # or latest tag

uv sync
./netllm init
./netllm serve
```

Dashboard: http://127.0.0.1:11400/ui/

### Homebrew

CLI agent background service (not the menubar app):

```bash
brew tap matthewdcage/netllm https://github.com/matthewdcage/llm-swarm-router
brew install netllm
brew services start netllm   # background agent
```

Logs: `$(brew --prefix)/var/log/netllm.log`

### GitHub DMG (when notarized)

Once [Developer ID notarization](macos-code-signing.md) is enabled in CI, the release DMG will return as the drag-to-Applications path:

1. Download `llm-swarm-router.dmg` from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases/latest).
2. Install with the bundled terminal installer (not drag-only on macOS 26):

```bash
INSTALLER="/Applications/llm-swarm-router.app/Contents/Resources/Scripts/macos-app-install.sh"
chmod +x "$INSTALLER"
"$INSTALLER" --dmg ~/Downloads/llm-swarm-router.dmg
```

**Today:** ad-hoc DMGs may fail Gatekeeper on macOS 26 — prefer **build from source** above. See [Gatekeeper troubleshooting](macos-troubleshooting.md#gatekeeper-blocks-install-or-launch).

### Repo maintainer upgrade helper

From a git checkout only:

```bash
cd /path/to/llm-swarm-router
./scripts/upgrade-mac-app.sh ~/Downloads/llm-swarm-router.dmg
# or after local build:
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app
```

## Menubar actions

| Item | Behavior |
|------|----------|
| Agent status | Running/stopped on port 11400; peer count inline when connected |
| Start / Stop Agent | Supervises `netllm serve -q` |
| Routing Stats | Per-backend health (●/○), peer count, role, model preview |
| Open Status Page | `http://127.0.0.1:11400/` |
| Open oMLX Admin | `http://127.0.0.1:8080/admin` when oMLX is installed |
| Open Dashboard | Local web UI at `/ui/` (same on Linux/Windows) |
| Copy Client Env | OpenAI + Anthropic env vars for editors |
| Check for Updates… | Poll GitHub for stable releases (downloads ad-hoc DMG until notarized — may not install on macOS 26+) |
| Settings… (⌘,) | Full `config.toml` editor + live status, backends, models, peers, doctor/test/gateway |

## In-app updates (Applications install)

When **llm-swarm-router** is under `/Applications/` (not a Stage/dev build):

1. **Menubar → Updates → Check for Updates…** or wait for the hourly background check.
2. When an update is available, choose **Download Update** (verifies size + SHA256 sidecar when published).
3. Choose **Install Update…** — the agent stops, the app quits, and the bundled `macos-app-install.sh` replaces the app and relaunches it.

**macOS 26+ note:** in-app update installs the GitHub release DMG. Until that DMG is notarized, prefer **build from source + install script** for upgrades.

Disable automatic checks in **Settings → UI → Check for updates automatically** or in `config.toml`:

```toml
[ui]
check_for_updates_automatically = false
```

The web dashboard at `/ui/` also shows version info and download links (via agent proxy to GitHub — no browser CORS). Homebrew installs should use `brew upgrade netllm` instead of in-app DMG install.

Settings tabs mirror CLI scope: **Overview**, **Backends** (`discover`), **Models**, **Peers** (`peers`), **Agent/Discovery/Swarm/Routing/UI** config sections, **Doctor & Test** tools. Saves via `netllm config import`; reads via `netllm config export`.

The app runs doctor via the **bundled** `netllm-cli` inside the `.app` (not your terminal PATH). Doctor skips “global CLI not on PATH” and “port in use by agent” when the menubar app is supervising the agent. For terminals, the app installs `~/.config/netllm/bin/netllm`, add that directory to PATH if you want `netllm` in every shell without `uv tool install`.

## Lifecycle commands

```bash
netllm start      # macOS app control socket or brew services
netllm stop
netllm restart
netllm serve      # foreground, still works for dev/CI
```

## Brand assets

Source files live in `assets/` (see `assets/README.md`):

| Asset | Role |
|-------|------|
| `llm-swam-router-icon.png` | Transparent: menubar template icon |
| `llm-swam-router-icon-black-bg.png` | Finder / Dock `.icns` source |
| Other PNG + SVG variants | Bundled under `Contents/Resources/Brand/` |

`build.sh` runs `Scripts/build-icons.sh` to produce `AppIcon.icns` and scaled menubar PNGs.
Verify with `scripts/test-brand-icons.sh` (also run as part of `test-menubar-e2e.sh`).

## Test install like an end user (maintainers)

```bash
./scripts/emulate-user-install-mac.sh
```

That builds a release Stage app, runs `macos-app-install.sh --source`, and launches from Applications.

## Build from source (developers)

```bash
uv sync
uv pip install venvstacks
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app
scripts/test-menubar-e2e.sh
```

Optional DMG for maintainers (ad-hoc until notarized): `packaging/scripts/create-dmg.sh`

See [packaging/README.md](../packaging/README.md) for venvstacks export details.

## Coexistence with oMLX

- **oMLX** manages inference on `:8080` (models, KV cache, admin dashboard).
- **netllm** routes editors to local backends and LAN peers on `:11400`.
- Config dirs are separate: `~/.omlx/` vs `~/.config/netllm/`.

Wire editors to netllm, see [editor-integration.md](editor-integration.md). Issues: [macos-troubleshooting.md](macos-troubleshooting.md).

## macOS code map (contributors)

| Component | Path |
|-----------|------|
| Menubar app (Swift) | [`apps/netllm-mac/`](../apps/netllm-mac/) |
| Python bundling + release scripts | [`packaging/`](../packaging/): see [packaging/README.md](../packaging/README.md) |
| CLI lifecycle (app socket, Homebrew) | [`packages/netllm-cli/src/netllm_cli/lifecycle/darwin.py`](../packages/netllm-cli/src/netllm_cli/lifecycle/darwin.py) |
| Shared agent core | [`packages/netllm-agent/`](../packages/netllm-agent/) |
