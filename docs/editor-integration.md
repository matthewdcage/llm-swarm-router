# Editor integration

Point AI coding tools at your local **netllm** agent instead of a cloud API.

## Prerequisites

```bash
./netllm serve          # agent on http://127.0.0.1:11400
./netllm models         # note an exact model ID from the table
curl -sf http://127.0.0.1:11400/health && echo ok
```

Shared client env:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

## Cursor

1. **Cursor Settings** → **Models**
2. Add OpenAI-compatible override:
   - Base URL: `http://127.0.0.1:11400/v1`
   - API key: `netllm-local`
   - Model: exact ID from `./netllm models`
3. Select that model in chat/composer
4. Verify: `./netllm test --model <your-model>`

## Claude Code

- Slash commands: `/netllm-setup`, `/netllm-connect`, `/netllm-swarm`, `/netllm-doctor`
- Project guide: [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md)

**OpenAI-compatible routing** (same as Cursor):

```bash
export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
export OPENAI_API_KEY=netllm-local
```

**Native Anthropic Messages API** (routes to local backends via translation):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
export ANTHROPIC_API_KEY=netllm-local
```

Model ID must match `./netllm models` exactly. Verify: `./netllm test --api anthropic --model <id>`.

For cloud Anthropic failover, set a real `ANTHROPIC_API_KEY` and add an `[[routing.backends]]` with `provider = "anthropic"` in config (see [config.example.toml](../config.example.toml)).

## Codex

- Reads [AGENTS.md](../AGENTS.md) from repo root automatically
- Skills in `.agents/skills/` for setup and troubleshoot workflows
- Same `OPENAI_BASE_URL` / `OPENAI_API_KEY` exports as above

## VS Code + GitHub Copilot

When your setup supports a custom OpenAI-compatible endpoint, use the same base URL, key, and model ID. Copilot cloud models are unrelated to this path.

## Honcho

See [honcho-integration.md](honcho-integration.md). Use `http://host.docker.internal:11400/v1` when Honcho runs in Docker and netllm on the host.

## LAN / swarm gateway

```bash
./netllm peers
export OPENAI_BASE_URL=http://<gateway-ip>:11400/v1
./netllm models --url http://<gateway-ip>:11400
```

## Agent-assisted setup

In Claude Code, run `/netllm-setup` then `/netllm-connect`. In Cursor or Codex, ask to "set up netllm" — the agent loads skills from `.cursor/skills/` or `.agents/skills/`.

Detailed reference: `.agents/skills/netllm-connect-editor/references/editor-settings.md`

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Agent unreachable | `./netllm serve` |
| No models | Start Ollama/oMLX; `./netllm discover` |
| Editor errors on model | Model string must match `./netllm models` exactly |
| Full diagnostic | `./netllm doctor` or `/netllm-doctor` |
