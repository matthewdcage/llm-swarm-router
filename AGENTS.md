# Agent & developer guide

## Project overview

**swarm-llm (netllm)** is a mesh router for local LLM backends. Each host runs a lightweight agent that discovers oMLX, Ollama, and LM Studio on localhost, finds sibling agents on the LAN via mDNS, and exposes a single OpenAI-compatible endpoint at `http://<host>:11400/v1`.

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
| `./netllm serve --host 0.0.0.0` | LAN + swarm — other machines can reach this agent |
| `./netllm status` | Agent, backends, swarm peers |
| `./netllm models` | Routed model catalog |
| `./netllm models --lan` | Models on remote LAN agents |
| `./netllm peers` | mDNS browse for swarm agents |
| `./netllm discover` | Probe oMLX / Ollama / LM Studio on localhost |
| `./netllm test` | 1-token latency diagnose |
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

Default provider ports: oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`.

## Agent skills

Load the matching skill when the user asks to install, connect an editor, set up a swarm, or troubleshoot netllm. In Claude Code, use slash commands (e.g. `/netllm-setup`).

| Skill | Triggers | Canonical path |
|-------|----------|------------------|
| `netllm-setup` | install swarm-llm, set up netllm, `/netllm-setup` | `.agents/skills/netllm-setup/SKILL.md` |
| `netllm-connect-editor` | connect Cursor, wire Claude Code, Codex local model, `/netllm-connect` | `.agents/skills/netllm-connect-editor/SKILL.md` |
| `netllm-swarm` | LAN swarm, multi-Mac, `/netllm-swarm` | `.agents/skills/netllm-swarm/SKILL.md` |
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

- Conventional commit messages; focus on why
- Do not commit `.cursor/mcp.json`, `.cursor/hooks/state/`, or secrets
- Do not commit unless the user explicitly asks

## Do not

- Edit user `.env` files or replace keys/values unless explicitly directed
- Delete files — move to `archived/` and log the action (project convention)
- Commit secrets, API keys, or real credentials
- Assume `netllm` is on PATH — prefer `./netllm` from repo root in instructions
- Skip `./netllm doctor` before declaring setup complete
- Auto-edit user editor `settings.json` without explicit consent

## Learned facts

- `./netllm` wrapper runs `uv run --directory $ROOT netllm` — no global install needed
- mDNS (swarm discovery) requires zeroconf from `uv sync`; reinstall if `netllm doctor` reports mDNS unavailable
- `serve` on loopback (`127.0.0.1`) blocks LAN peers — use `--host 0.0.0.0` for swarm
- Set `swarm.cluster_token` when listening on `0.0.0.0` on untrusted networks
- Opening `http://127.0.0.1:11400/` in a browser returns help JSON — not an error
