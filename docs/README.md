# Documentation index

User-facing guides for **llm-swarm-router** (netllm). For developers and agents, start with [AGENTS.md](../AGENTS.md) at the repo root.

## By platform

| Platform | Install | Troubleshooting |
|----------|---------|-----------------|
| **macOS** | [macos-install.md](macos-install.md) | [macos-troubleshooting.md](macos-troubleshooting.md) |
| **Linux** | [linux-install.md](linux-install.md) | [linux-troubleshooting.md](linux-troubleshooting.md) |
| **Windows** | [windows-install.md](windows-install.md) | [windows-troubleshooting.md](windows-troubleshooting.md) |

Overview: [platform-matrix.md](platform-matrix.md)

## Releases

| Topic | Doc |
|-------|-----|
| Latest release | [v0.4.4.0 release notes](release-notes/v0.4.4.0.md) · [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases) |
| Prior alpha (Linux/Windows) | [v0.2.2 release notes](release-notes/v0.2.2-alpha.md) |
| Linux/Windows QA checklist | [solutions/linux-windows-alpha-qa.md](solutions/linux-windows-alpha-qa.md) |

## Cross-platform

| Topic | Doc |
|-------|-----|
| Wire Cursor, Claude Code, Codex, Honcho | [editor-integration.md](editor-integration.md) (start at **Client configuration**) |
| Honcho (Docker / deriver / connectors) | [honcho-integration.md](honcho-integration.md) |
| Quick diagnostic | `./netllm doctor` from repo root or global install |

## Developers & agents

| Topic | Doc |
|-------|-----|
| CI, macOS build, release | [ci-and-release.md](ci-and-release.md) |
| macOS Developer ID + notarization | [macos-code-signing.md](macos-code-signing.md) |
| Pre-push verification | `scripts/verify-before-pr.sh` from repo root |
| DOX hierarchy (agents) | [AGENTS.md](../AGENTS.md) Child DOX Index |

## Repository map

| Path | Purpose | DOX |
|------|---------|-----|
| [`packages/`](../packages/) | Python source of truth (uv workspace) | [packages/AGENTS.md](../packages/AGENTS.md) |
| [`apps/`](../apps/) | Native apps: macOS menubar today (`apps/netllm-mac/`) | [apps/AGENTS.md](../apps/AGENTS.md) |
| [`packaging/`](../packaging/) | Release builds per OS: [packaging/README.md](../packaging/README.md) | [packaging/AGENTS.md](../packaging/AGENTS.md) |
| [`docs/`](.) | Install, troubleshoot, and editor guides (this folder) | [docs/AGENTS.md](AGENTS.md) |
| [`tests/`](../tests/) | Cross-package integration tests | [tests/AGENTS.md](../tests/AGENTS.md) |
| [`scripts/`](../scripts/) | CI, skill sync, install emulation | (root [AGENTS.md](../AGENTS.md)) |
| [`.agents/skills/`](../.agents/skills/) | Canonical agent skills (sync to tool paths) | [.agents/AGENTS.md](../.agents/AGENTS.md) |

Architecture (packages, commands, env): [AGENTS.md](../AGENTS.md). Contributing: [CONTRIBUTING.md](../CONTRIBUTING.md). Help routing: [SUPPORT.md](../SUPPORT.md).
