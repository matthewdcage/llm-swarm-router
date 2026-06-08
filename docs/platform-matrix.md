# Platform matrix — netllm

Single reference for install channels, UI surfaces, and stability tiers across macOS, Linux, and Windows.

## Stability tiers

| Tier | Platforms | Expectation |
|------|-----------|-------------|
| **Stable** | macOS | DMG menubar app on every published GitHub Release |
| **Beta** | Linux, Windows | deb/rpm/zip on every published Release; breaking changes called out in release notes |
| **Core** | All | HTTP API contract at `:11400` — additive changes only |

## Install and lifecycle

| Platform | Recommended install | Background agent | `netllm start/stop/restart` |
|----------|---------------------|------------------|----------------------------|
| **macOS** | [DMG](https://github.com/matthewdcage/llm-swarm-router/releases) → Applications | Menubar app supervises agent | App control socket or Homebrew `brew services` |
| **macOS** | Homebrew `brew install netllm` | `brew services start netllm` | Homebrew |
| **Linux** | `.deb` / `.rpm` from Releases | systemd user unit `netllm` | `linux-systemd` when package installed |
| **Windows** | `netllm-*-windows-x64.zip` | `NetllmAgent` Windows service | `windows-service` after `install-service.ps1` |
| **All** | Source: `uv sync` + `./netllm serve` | Foreground terminal | N/A — use `netllm serve` |

Details: [menubar-app.md](menubar-app.md), [linux-install.md](linux-install.md), [windows-install.md](windows-install.md).

## UI surfaces

| Surface | macOS | Linux | Windows |
|---------|-------|-------|---------|
| **Web dashboard** | http://127.0.0.1:11400/ui/ | Same | Same |
| **Menubar / tray** | Native Swift menubar | — | — |
| **CLI** | `netllm status`, `discover`, `env`, `doctor` | Same | Same |
| **Config edit** | Settings window or `netllm config-edit` | `netllm config-edit` | `netllm config-edit` |

Menubar **Open Dashboard** opens the same `/ui/` page as Linux and Windows browsers.

## Discovery defaults (`netllm init`)

| OS | Default `discovery.providers` |
|----|------------------------------|
| macOS | `omlx`, `ollama`, `lmstudio`, `vllm` |
| Linux / Windows | `ollama`, `lmstudio`, `vllm` |

Agent lifespan runs provider discovery on start and persists URLs to config (all platforms).

## Release assets (one GitHub Release page)

| Asset | Platform |
|-------|----------|
| `llm-swarm-router.dmg` | macOS (stable) |
| `netllm_*_amd64.deb` | Linux (beta) |
| `netllm-*.rpm` | Linux (beta) |
| `netllm-*-windows-x64.zip` | Windows (beta) |
| `winget-manifest.yaml` | Windows winget SHA snippet |

Built by [.github/workflows/release.yml](../.github/workflows/release.yml) on `release: published`.

## Logs

| Platform | Default log directory | File |
|----------|----------------------|------|
| macOS | `~/Library/Application Support/netllm/logs` | `agent.log` |
| Linux | `$XDG_STATE_HOME/netllm/logs` or `~/.local/state/netllm/logs` | `agent.log` |
| Windows | `%LOCALAPPDATA%\netllm\logs` | `agent.log` |

Override with `[ui] log_dir` in `config.toml`.

## Wire editors

```bash
netllm env          # print export lines
# or copy from dashboard → Copy client env
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

See [editor-integration.md](editor-integration.md).
