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

Full guide: [honcho-integration.md](honcho-integration.md).

**Bottom line:** Change Honcho's `base_url` and connector env to netllm once (`http://127.0.0.1:11400/v1`, or `http://host.docker.internal:11400/v1` from Docker). Keep model names as-is. Configure oMLX, Ollama, LM Studio, and swarm peers in `~/.config/netllm/config.toml` only, not in Honcho. Once LAN peers are up, routing is automatic; Honcho does not need per-machine URLs.

```toml
# Honcho deriver / dialectic overrides (example)
base_url = "http://host.docker.internal:11400/v1"
api_key = "netllm-local"
```

## LAN / swarm gateway

```bash
./netllm peers
export OPENAI_BASE_URL=http://<gateway-ip>:11400/v1
./netllm models --url http://<gateway-ip>:11400
```

## Agent-assisted setup

In Claude Code, run `/netllm-setup` then `/netllm-connect`. In Cursor or Codex, ask to "set up netllm", the agent loads skills from `.cursor/skills/` or `.agents/skills/`.

Detailed reference: `.agents/skills/netllm-connect-editor/references/editor-settings.md`

## Local-only routing

Send `X-Netllm-Local-Only: 1` on OpenAI or Anthropic requests to restrict routing to local backends only (no LAN peers, no cloud inject). Useful for privacy-sensitive prompts when the mesh also has remote peers or optional cloud failover configured.

```bash
curl -s http://127.0.0.1:11400/v1/chat/completions \
  -H "Authorization: Bearer netllm-local" \
  -H "X-Netllm-Local-Only: 1" \
  -H "Content-Type: application/json" \
  -d '{"model":"<local-model>","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
```

## Cloud failover (optional)

When no local backend serves a model, netllm can inject OpenAI or Anthropic cloud backends if a **real** API key is available (environment variable or macOS Keychain via the menubar app). The placeholder `netllm-local` never enables cloud routing.

- OpenAI: set `OPENAI_API_KEY` or save a key in **Settings → Routing → Cloud failover** (macOS app)
- Anthropic: set `ANTHROPIC_API_KEY` or the Keychain field above
- Explicit `[[routing.backends]]` rows in `~/.config/netllm/config.toml` still override discovery

Cloud paths are opt-in; default mesh behavior remains local-first.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Agent unreachable | `./netllm serve` (source) or `netllm start` (packaged install) |
| No models | Start Ollama/LM Studio/vLLM (oMLX on macOS); `./netllm discover` |
| Editor errors on model | Model string must match `./netllm models` exactly |
| Full diagnostic | `./netllm doctor` or `/netllm-doctor` |

Platform-specific guides: [macos-troubleshooting.md](macos-troubleshooting.md) · [linux-troubleshooting.md](linux-troubleshooting.md) · [windows-troubleshooting.md](windows-troubleshooting.md)
