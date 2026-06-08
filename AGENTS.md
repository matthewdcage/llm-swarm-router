# Agent & developer guide

## Project overview

**swarm-llm (netllm)** is a mesh router for local LLM backends. Each host runs a lightweight agent that discovers oMLX (macOS), Ollama, LM Studio, and vLLM on localhost, finds sibling agents on the LAN via mDNS, and exposes dual API surfaces: OpenAI-compatible `http://<host>:11400/v1` and Anthropic Messages API `http://<host>:11400/v1/messages` (with translation to local backends).

Tech stack: Python 3.11+, [uv](https://docs.astral.sh/uv/) workspace monorepo, FastAPI agent, Typer CLI.

## Architecture

| Package | Path | Role |
|---------|------|------|
| netllm-core | `packages/netllm-core/` | Routing, health cache, config |
| netllm-sdk-openai | `packages/netllm-sdk-openai/` | OpenAI SDK upstream adapter |
| netllm-sdk-anthropic | `packages/netllm-sdk-anthropic/` | Anthropic SDK upstream adapter |
| netllm-discovery | `packages/netllm-discovery/` | Local scan, swarm registry, mDNS |
| netllm-agent | `packages/netllm-agent/` | FastAPI — `/v1/*`, `/netllm/v1/*`, `/metrics` |
| netllm-cli | `packages/netllm-cli/` | Typer CLI |

Deeper notes: [docs/architecture-reference.md](docs/architecture-reference.md). Honcho integration: [docs/honcho-integration.md](docs/honcho-integration.md).

## Key commands

Prefer `./netllm` from the repo root — works without global PATH (`uv run` wrapper in [netllm](netllm)).

| Command | Purpose |
|---------|---------|
| `uv sync` | Install workspace dependencies |
| `./netllm init` | Write config, scan local providers, optional global CLI |
| `./netllm install` | Global `netllm` via `uv tool install` + shell PATH |
| `./netllm serve` | Start agent (foreground, default `127.0.0.1:11400`) |
| `./netllm start` / `stop` / `restart` | Background agent (macOS app, Homebrew, Linux systemd, Windows service) |
| `./netllm serve --host 0.0.0.0` | LAN + swarm — other machines can reach this agent |
| `./netllm status` | Agent, backends, swarm peers |
| `./netllm models` | Routed model catalog |
| `./netllm models --lan` | Models on remote LAN agents |
| `./netllm peers` | mDNS browse for swarm agents |
| `./netllm discover` | Probe oMLX / Ollama / LM Studio / vLLM on localhost |
| `./netllm test` | 1-token latency diagnose (OpenAI backends) |
| `./netllm test --api anthropic` | 1-token Messages API probe via agent |
| `./netllm gateway` | Promote agent role to gateway |
| `./netllm doctor` | PATH, mDNS, backend misconfig checks |
| `./netllm config-edit` | Open `config.toml` in `$EDITOR` |
| `uv run pytest tests/ -v` | Run tests |
| `uv run ruff check packages/ tests/` | Lint |
| `scripts/agent-verify-setup.sh` | Health + models check after setup |
| `scripts/sync-agent-skills.sh` | Sync `.agents/skills/` to other tool paths |

## Environment

Config: `~/.config/netllm/config.toml` (created by `./netllm init`). Example: [config.example.toml](config.example.toml).

Wire any OpenAI-compatible client:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

Native Anthropic Messages API (Claude Code, etc.):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

Use a real `ANTHROPIC_API_KEY` only for cloud failover; local mesh uses `netllm-local`.

Default provider ports: oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`.

## Linux and Windows

| Platform | Install doc | Background agent | UI |
|----------|-------------|------------------|-----|
| Linux | [docs/linux-install.md](docs/linux-install.md) | `systemctl --user enable --now netllm` (deb/rpm) | http://127.0.0.1:11400/ui/ |
| Windows | [docs/windows-install.md](docs/windows-install.md) | `NetllmAgent` service via packaged zip | http://127.0.0.1:11400/ui/ |

Cross-platform install matrix: [docs/platform-matrix.md](docs/platform-matrix.md). Agent graph wiki: `graphify-out/wiki/index.md` (after `graphify update .`).

## macOS menubar app

Native app (oMLX-style): [docs/menubar-app.md](docs/menubar-app.md).

| Channel | Install |
|---------|---------|
| DMG | GitHub Releases → drag `netllm-mac.app` to Applications |
| Homebrew | `brew install netllm` + `brew services start netllm` |
| Source | `./netllm serve` (unchanged dev path) |

Build: `apps/netllm-mac/Scripts/build.sh release` (requires `venvstacks` + `uv sync`).

## SDK maintenance

Vendor SDKs are isolated in `netllm-sdk-openai` and `netllm-sdk-anthropic` only — `netllm-core` never imports `openai` or `anthropic`.

**Bump checklist** (one package per PR):

1. Edit `anthropic>=…` or `openai>=…` in the matching `packages/netllm-sdk-*/pyproject.toml`
2. `uv sync`
3. `uv run pytest packages/netllm-sdk-anthropic/tests/ tests/test_anthropic_bridge.py tests/test_agent.py -v`
4. Read upstream SDK changelog; adjust only `client.py` in that package if needed

## Agent skills

Load the matching skill when the user asks to install, connect an editor, set up a swarm, or troubleshoot netllm. In Claude Code, use slash commands (e.g. `/netllm-setup`).

| Skill | Triggers | Canonical path |
|-------|----------|------------------|
| `netllm-setup` | install swarm-llm, set up netllm, `/netllm-setup` | `.agents/skills/netllm-setup/SKILL.md` |
| `netllm-connect-editor` | connect Cursor, wire Claude Code, Codex local model, `/netllm-connect` | `.agents/skills/netllm-connect-editor/SKILL.md` |
| `netllm-swarm` | LAN swarm, multi-machine, `/netllm-swarm` | `.agents/skills/netllm-swarm/SKILL.md` |
| `netllm-doctor` | netllm broken, no models, agent unreachable, `/netllm-doctor` | `.agents/skills/netllm-doctor/SKILL.md` |

Tool-specific copies: `.claude/skills/`, `.cursor/skills/`, `.github/skills/`. Keep in sync via `scripts/sync-agent-skills.sh`.

Editor wiring reference: [docs/editor-integration.md](docs/editor-integration.md).

## Code style

- Python 3.11+, line length 88 (ruff)
- Type checking: basedpyright, mode `standard`
- Imports: ruff isort (`E`, `F`, `I`, `UP` rules)
- Match existing package layout and Typer/Rich CLI patterns in `netllm-cli`

## Testing

- Runner: pytest (`tests/`, asyncio mode auto)
- CI: `uv sync` → `uv run pytest tests/ -v` → `uv run ruff check packages/ tests/`
- Add tests only for real behavior; avoid trivial assertions

## Git workflow

Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md) for fork/PR workflow, issue templates, and review expectations.

- Conventional commit messages; focus on why
- Do not commit `.cursor/mcp.json`, `.cursor/hooks/state/`, or secrets
- Do not commit unless the user explicitly asks (agents); human contributors open PRs per CONTRIBUTING.md

## Do not

- Edit user `.env` files or replace keys/values unless explicitly directed
- Delete files — move to `archived/` and log the action (project convention)
- Commit secrets, API keys, or real credentials
- Assume `netllm` is on PATH — prefer `./netllm` from repo root in instructions
- Skip `./netllm doctor` before declaring setup complete
- Auto-edit user editor `settings.json` without explicit consent

## Learned facts

- Local web dashboard at http://127.0.0.1:11400/ui/ on all platforms; macOS menubar has **Open Dashboard**
- Linux/Windows beta use `/ui/` + CLI; macOS stable adds menubar app — same agent core
- Published GitHub Releases attach DMG + deb + rpm + zip via `.github/workflows/release.yml`
- `./netllm` wrapper runs `uv run --directory $ROOT netllm` — no global install needed
- mDNS (swarm discovery) requires zeroconf from `uv sync`; reinstall if `netllm doctor` reports mDNS unavailable
- `serve` on loopback (`127.0.0.1`) blocks LAN peers — use `--host 0.0.0.0` for swarm
- Set `swarm.cluster_token` when listening on `0.0.0.0` on untrusted networks
- Opening `http://127.0.0.1:11400/` in a browser returns help JSON — not an error
