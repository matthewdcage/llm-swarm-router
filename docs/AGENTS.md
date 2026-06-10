# docs — user and developer documentation

## Purpose

User-facing install, troubleshoot, editor wiring, platform matrix, CI/release, and SDK version guides. Index: [README.md](README.md).

## Ownership

| Area | Key files |
|------|-----------|
| Platform install | `macos-install.md`, `linux-install.md`, `windows-install.md` |
| Troubleshooting | `macos-troubleshooting.md`, `linux-troubleshooting.md`, `windows-troubleshooting.md` |
| Cross-platform | `platform-matrix.md`, `editor-integration.md`, `honcho-integration.md` |
| macOS signing | `macos-code-signing.md` |
| Release / CI | `ci-and-release.md`, `release-notes/` |
| Learnings | `solutions/` |
| SDK maintenance | `sdk-versions.md` |

Parent rail: [../AGENTS.md](../AGENTS.md).

## Local Contracts

- User docs stay plain and actionable; agent commands use `./netllm` from repo root
- Release tag must match root `pyproject.toml` version
- Do not commit `.cursor/plans/`, `.cursor/outreach/`, or coordinator drafts here
- User-facing command examples use placeholders (`/path/to/llm-swarm-router`, `~/Downloads/…`); never maintainer machine paths (`/Volumes/…`, named dev hardware)
- **macOS Gatekeeper (macOS 26+):** ad-hoc GitHub DMGs are rejected at launch; user-facing install guides lead with **clone tag → build → `macos-app-install.sh --source`** ([macos-install.md](macos-install.md), [macos-troubleshooting.md#gatekeeper-blocks-install-or-launch](macos-troubleshooting.md#gatekeeper-blocks-install-or-launch)); notarized DMG path documented for when signing ships ([macos-code-signing.md](macos-code-signing.md))
- **macOS upgrade (users):** build from source + `--source` install on macOS 26+; menubar **Updates** and `--dmg` install when notarized; bundled `macos-app-install.sh` under `Contents/Resources/Scripts/`; `./scripts/upgrade-mac-app.sh` is **repo-checkout only**
- **Release notes:** macOS install order documented in [ci-and-release.md](ci-and-release.md); canonical example [release-notes/v0.3.0.1.md](release-notes/v0.3.0.1.md)

## Work Guidance

- New platform behavior: update install + troubleshooting + platform-matrix together
- SDK bumps: update `sdk-versions.md` in same PR as package dep change
- Release notes go under `release-notes/` with version in filename; macOS blocks follow v0.3.0.1 pattern (in-app → bundled installer → optional repo script); menubar agent-start fixes: [release-notes/v0.3.0.2.md](release-notes/v0.3.0.2.md)
- macOS signing/notarization changes: update `macos-code-signing.md`, `ci-and-release.md`, and install/troubleshooting Gatekeeper sections together
- Web dashboard / admin-access behavior: document in `macos-troubleshooting.md` (Swarm or dashboard section) when agent `admin.py` or `static/dashboard.js` contracts change

## Verification

- Links resolve from README index
- Command snippets match current CLI (`./netllm doctor`, etc.)
- macOS upgrade snippets do not assume a git clone unless labeled “repo checkout”

## Child DOX Index

| Path | Contract |
|------|----------|
| [`release-notes/`](release-notes/) | Versioned release notes (no AGENTS.md — filenames are the index) |
| [`solutions/`](solutions/) | Durable QA and workflow learnings |

Nested folders are content collections; add child AGENTS.md only if a subtree gains its own release or editorial workflow.
