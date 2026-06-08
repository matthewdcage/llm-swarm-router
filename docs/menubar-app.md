# netllm macOS Menubar App

Native menubar wrapper for the netllm mesh router (oMLX-style). The app supervises the
existing Python agent on port **11400** — it does not replace oMLX inference on port 8080.

## Install channels

### DMG (recommended for desktop users)

1. Download `netllm-mac.dmg` from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases).
2. Drag **netllm** to Applications.
3. Launch from Applications — the netllm bee logo appears in the menu bar.
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
| Copy Client Env | OpenAI + Anthropic env vars for editors |
| Settings… (⌘,) | Full `config.toml` editor + live status, backends, models, peers, doctor/test/gateway |

Settings tabs mirror CLI scope: **Overview**, **Backends** (`discover`), **Models**, **Peers** (`peers`), **Agent/Discovery/Swarm/Routing/UI** config sections, **Doctor & Test** tools. Saves via `netllm config import`; reads via `netllm config export`.

## Lifecycle commands

```bash
netllm start      # macOS app control socket or brew services
netllm stop
netllm restart
netllm serve      # foreground — still works for dev/CI
```

## Brand assets

Source files live in `assets/` (see `assets/README.md`):

| Asset | Role |
|-------|------|
| `llm-swam-router-icon.png` | Transparent — menubar template icon |
| `llm-swam-router-icon-black-bg.png` | Finder / Dock `.icns` source |
| Other PNG + SVG variants | Bundled under `Contents/Resources/Brand/` |

`build.sh` runs `Scripts/build-icons.sh` to produce `AppIcon.icns` and scaled menubar PNGs.
Verify with `scripts/test-brand-icons.sh` (also run as part of `test-menubar-e2e.sh`).

## Build from source

```bash
uv sync
apps/netllm-mac/Scripts/build.sh release
open apps/netllm-mac/build/Stage/netllm-mac.app

# End-to-end verification
scripts/test-menubar-e2e.sh
```

Optional DMG:

```bash
packaging/scripts/create-dmg.sh
```

## Coexistence with oMLX

- **oMLX** manages inference on `:8080` (models, KV cache, admin dashboard).
- **netllm** routes editors to local backends and LAN peers on `:11400`.
- Config dirs are separate: `~/.omlx/` vs `~/.config/netllm/`.

Wire editors to netllm — see [editor-integration.md](editor-integration.md).
