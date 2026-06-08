# Documentation index

User-facing guides for **llm-swarm-router** (netllm). For developers and agents, start with [AGENTS.md](../AGENTS.md) at the repo root.

## By platform

| Platform | Install | Troubleshooting |
|----------|---------|-----------------|
| **macOS** | [macos-install.md](macos-install.md) | [macos-troubleshooting.md](macos-troubleshooting.md) |
| **Linux** | [linux-install.md](linux-install.md) | [linux-troubleshooting.md](linux-troubleshooting.md) |
| **Windows** | [windows-install.md](windows-install.md) | [windows-troubleshooting.md](windows-troubleshooting.md) |

Overview: [platform-matrix.md](platform-matrix.md)

## Cross-platform

| Topic | Doc |
|-------|-----|
| Wire Cursor, Claude Code, Codex, Honcho | [editor-integration.md](editor-integration.md) |
| Honcho (Docker / deriver) | [honcho-integration.md](honcho-integration.md) |
| Quick diagnostic | `./netllm doctor` from repo root or global install |

## Repository map

| Path | Purpose |
|------|---------|
| [`packages/`](../packages/) | Python source of truth (uv workspace) |
| [`apps/`](../apps/) | Native apps — macOS menubar today (`apps/netllm-mac/`) |
| [`packaging/`](../packaging/) | Release builds per OS — [packaging/README.md](../packaging/README.md) |
| [`docs/`](.) | Install, troubleshoot, and editor guides (this folder) |
| [`tests/`](../tests/) | Cross-package integration tests |
| [`scripts/`](../scripts/) | CI, skill sync, install emulation |
| [`.agents/skills/`](../.agents/skills/) | Canonical agent skills (sync to `.claude/`, `.cursor/`, `.github/`) |

Architecture (packages, commands, env): [AGENTS.md](../AGENTS.md). Contributing: [CONTRIBUTING.md](../CONTRIBUTING.md). Help routing: [SUPPORT.md](../SUPPORT.md).
