# Platform matrix: netllm

Single reference for install channels, UI surfaces, and stability tiers across macOS, Linux, and Windows.

**Release builds:** macOS DMG/venvstacks, Linux deb/rpm, and Windows zip scripts live under [packaging/README.md](../packaging/README.md) (`packaging/`, `Formula/` for Homebrew).

## Documentation by platform

| Platform | Install | Troubleshooting |
|----------|---------|-----------------|
| **macOS** | [macos-install.md](macos-install.md) | [macos-troubleshooting.md](macos-troubleshooting.md) |
| **Linux** | [linux-install.md](linux-install.md) | [linux-troubleshooting.md](linux-troubleshooting.md) |
| **Windows** | [windows-install.md](windows-install.md) | [windows-troubleshooting.md](windows-troubleshooting.md) |

Cross-platform: [editor-integration.md](editor-integration.md) · Agent help: `./netllm doctor`

## Stability tiers

| Tier | Platforms | Expectation |
|------|-----------|-------------|
| **Stable** | macOS | DMG menubar app on every published GitHub Release |
| **Alpha** | Linux, Windows | First deb/rpm/zip packages on each Release; breaking changes and install UX still settling: see release notes |
| **Core** | All | HTTP API contract at `:11400`: additive changes only |

## Install and lifecycle

| Platform | Recommended install | Background agent | `netllm start/stop/restart` |
|----------|---------------------|------------------|----------------------------|
| **macOS** | [DMG](https://github.com/matthewdcage/llm-swarm-router/releases) → Applications | Menubar app supervises agent | App control socket or Homebrew `brew services` |
| **macOS** | Homebrew `brew install netllm` | `brew services start netllm` | Homebrew |
| **Linux** | `.deb` / `.rpm` from Releases | systemd user unit `netllm` | `linux-systemd` when package installed |
| **Windows** | `netllm-*-windows-x64.zip` | `NetllmAgent` Windows service | `windows-service` after `install-service.ps1` |
| **All** | Source: `uv sync` + `./netllm serve` | Foreground terminal | N/A: use `netllm serve` |

Install details: links in the table above.

## UI surfaces

| Surface | macOS | Linux | Windows |
|---------|-------|-------|---------|
| **Web dashboard** | http://127.0.0.1:11400/ui/ | Same | Same |
| **Menubar / tray** | Native Swift menubar + **Copy Client Env** | - |: |
| **CLI** | `netllm status`, `discover`, `doctor` | Same | Same |
| **Config edit** | Settings window or `netllm config-edit` | `netllm config-edit` | `netllm config-edit` |

Menubar **Open Dashboard** opens `/ui/`. **Copy Client Env** exports editor vars. Rebuild the menubar app if `/ui/` returns 404 (embedded agent stale).

## Discovery defaults (`netllm init`)

| OS | Default `discovery.providers` |
|----|------------------------------|
| macOS | `omlx`, `ollama`, `lmstudio`, `vllm` |
| Linux / Windows | `ollama`, `lmstudio`, `vllm` |

Agent lifespan runs provider discovery on start and persists URLs to config (all platforms).

## Release assets (one GitHub Release page)

Latest: [v0.2.3.5](https://github.com/matthewdcage/llm-swarm-router/releases/tag/v0.2.3.5) (macOS update UX + agent port recovery) · [v0.2.3.4](https://github.com/matthewdcage/llm-swarm-router/releases/tag/v0.2.3.4) (lifecycle gates) · [All releases](https://github.com/matthewdcage/llm-swarm-router/releases)

| Asset | Platform |
|-------|----------|
| `llm-swarm-router.dmg` | macOS (stable) |
| `netllm_*_amd64.deb` | Linux (alpha) |
| `netllm-*.rpm` | Linux (alpha) |
| `netllm-*-windows-x64.zip` | Windows (alpha) |
| `netllm.yaml` | Winget manifest snippet (SHA256 + URL for winget-pkgs PR) |

Built by [.github/workflows/release.yml](../.github/workflows/release.yml) on `release: published` (macOS DMG, Linux deb/rpm, Windows zip + winget manifest).

## Logs

| Platform | Default log directory | File |
|----------|----------------------|------|
| macOS | `~/Library/Application Support/netllm/logs` | `agent.log` |
| Linux | `$XDG_STATE_HOME/netllm/logs` or `~/.local/state/netllm/logs` | `agent.log` |
| Windows | `%LOCALAPPDATA%\netllm\logs` | `agent.log` |

Override with `[ui] log_dir` in `config.toml`.

## Wire editors

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
# macOS menubar: Copy Client Env · dashboard: Copy client env
```

See [editor-integration.md](editor-integration.md).
