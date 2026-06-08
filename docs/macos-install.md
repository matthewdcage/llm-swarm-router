# macOS install: netllm menubar app

Native menubar wrapper for **llm-swarm-router** (terminal CLI: `netllm`). The app supervises the
existing Python agent on port **11400**, it does not replace oMLX inference on port 8080.

**Troubleshooting:** [macos-troubleshooting.md](macos-troubleshooting.md) · **All platforms:** [platform-matrix.md](platform-matrix.md)

## Install channels

### DMG (recommended for desktop users)

1. Download `llm-swarm-router.dmg` from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases/latest).
2. Open the DMG and drag **llm-swarm-router** to **Applications**.
3. Launch from Applications, the llm-swarm-router bee logo appears in the menu bar (switches for light/dark menu bar).
4. Complete the welcome wizard (config path, LAN mode, auto-start).

The app installs a CLI shim at `~/.config/netllm/bin/netllm` for terminal control.

### Homebrew

```bash
brew tap matthewdcage/netllm https://github.com/matthewdcage/llm-swarm-router
brew install netllm
brew services start netllm   # background agent
```

Logs: `$(brew --prefix)/var/log/netllm.log`

### Developer / source (unchanged)

```bash
uv sync
./netllm init
./netllm serve
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
| Settings… (⌘,) | Full `config.toml` editor + live status, backends, models, peers, doctor/test/gateway |

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

## Test install like an end user (recommended for maintainers)

Quit any running menubar instance, then:

```bash
./scripts/emulate-user-install-mac.sh
```

That builds a release DMG, installs to `/Applications/llm-swarm-router.app`, and launches from there, the same path users follow after downloading from Releases.

Manual equivalent: open `dist/llm-swarm-router.dmg` → drag to Applications → launch from Applications (right-click **Open** once if Gatekeeper prompts).

## Build from source (developers only)

```bash
uv sync
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/create-dmg.sh
scripts/test-menubar-e2e.sh
```

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
| Python bundling + DMG | [`packaging/`](../packaging/): see [packaging/README.md](../packaging/README.md) |
| CLI lifecycle (app socket, Homebrew) | [`packages/netllm-cli/src/netllm_cli/lifecycle/darwin.py`](../packages/netllm-cli/src/netllm_cli/lifecycle/darwin.py) |
| Shared agent core | [`packages/netllm-agent/`](../packages/netllm-agent/) |
