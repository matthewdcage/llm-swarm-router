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

## Client configuration (all tools)

Use the same pattern for **Cursor, Claude Code, Codex, Honcho, Continue, Cline, curl, and custom apps**:

| Setting | Value |
|---------|--------|
| OpenAI-compatible base URL | `http://127.0.0.1:11400/v1` on the same machine, `http://<gateway-ip>:11400/v1` for a LAN gateway, or `http://host.docker.internal:11400/v1` when the client runs in Docker and netllm on the host |
| API key | `netllm-local` (placeholder; real keys only for optional cloud failover) |
| Model ID | Unchanged from your current setup; must match `./netllm models` exactly |
| Backend URLs (oMLX, Ollama, LM Studio, vLLM) | **`~/.config/netllm/config.toml` only** — discovery, Settings UI, or `[[routing.backends]]`. Do not keep per-machine URL lists in each client. |
| Multi-machine swarm | Run `./netllm serve --host 0.0.0.0` on each node; clients still use **one** netllm URL. Peers merge automatically (`./netllm peers`). Optional: `./netllm gateway` on one host, then point clients at that agent only. |

**Anthropic Messages API clients** (Claude Code native path, some agents): use `http://127.0.0.1:11400` (no `/v1`) and `ANTHROPIC_API_KEY=netllm-local`.

Verify after wiring: `./netllm test --model <your-model>` (add `--api anthropic` for Messages API).

Per-tool UI steps below; Honcho-specific connector sharding: [honcho-integration.md](honcho-integration.md).

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

Full guide: [honcho-integration.md](honcho-integration.md). Follow **Client configuration (all tools)** above, then set Honcho-specific overrides:

```toml
# deriver / dialectic (example)
[deriver.model_config.overrides]
base_url = "http://host.docker.internal:11400/v1"
api_key = "netllm-local"
```

Connectors: single `LLM_OPENAI_COMPATIBLE_BASE_URL` (not comma-separated URLs) plus `CONNECTOR_LLM_ROUTING_STRATEGY=batch_shard` when running parallel workers. See the Honcho guide for shard headers.

## LAN / swarm gateway

```bash
./netllm peers
export OPENAI_BASE_URL=http://<gateway-ip>:11400/v1
./netllm models --url http://<gateway-ip>:11400
```

## Agent-assisted setup

In Claude Code, run `/netllm-setup` then `/netllm-connect`. In Cursor or Codex, ask to "set up netllm", the agent loads skills from `.cursor/skills/` or `.agents/skills/`.

Detailed reference: `.agents/skills/netllm-connect-editor/references/editor-settings.md`

## Embeddings

`POST /v1/embeddings` is part of the OpenAI-compatible surface — point any embeddings client (RAG pipelines, Honcho deriver, LlamaIndex, LangChain) at the same base URL. Requests route to whichever backend serves the embedding model (Ollama, oMLX, LM Studio, vLLM, or a LAN peer agent) with the same failover and spillover as chat.

```bash
curl -s http://127.0.0.1:11400/v1/embeddings \
  -H "Authorization: Bearer netllm-local" \
  -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text:latest","input":"hello swarm"}'
```

Pick an embedding model from `GET /v1/models` — each entry carries a `capability` field (`chat`, `embedding`, `audio`, `rerank`, `other`). Chat requests against embedding/audio models are rejected with a clear `400` instead of failing upstream.

The Anthropic Messages API has no embeddings standard (Anthropic recommends third-party embedding providers), so Anthropic-wired clients use the OpenAI surface above for embeddings.

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
