# Hermes Agent integration guide

Use **netllm** as the OpenAI-compatible model backend for [Hermes Agent](https://github.com/NousResearch/hermes-agent) (NousResearch) — classic CLI, TUI (`hermes --tui`), and messaging gateway share one config.

This is **not** [Honcho](honcho-integration.md) (the memory platform). Hermes Agent is a separate product with its own `hermes` CLI.

Hermes follows the same client rules as every other tool: [editor-integration.md](editor-integration.md#client-configuration-all-tools). Summary:

1. Point `~/.hermes/config.yaml` at netllm once (`http://127.0.0.1:11400/v1`, or `http://host.docker.internal:11400/v1` from Docker).
2. Keep your **model name**; it must match `./netllm models` exactly.
3. Configure oMLX, Ollama, LM Studio, vLLM, and swarm peers in **`~/.config/netllm/config.toml`**, not in Hermes.
4. With swarm peers visible (`./netllm peers`), routing across machines is automatic.

## Prerequisites

```bash
./netllm serve
./netllm models         # note an exact model ID
curl -sf http://127.0.0.1:11400/health && echo ok
```

Hermes Agent installed (`hermes` on PATH). Install hint from the registry: `pip install hermes-agent` (see [Hermes docs](https://hermes-agent.nousresearch.com/docs/integrations/providers) for the current one-liner).

For the TUI: Node.js ≥ 20 (`hermes doctor` verifies). First `hermes --tui` launch installs `ui-tui/node_modules` once.

## Fastest path

```bash
./netllm connect hermes-agent          # prints ~/.hermes/config.yaml snippet
./netllm connect hermes-agent --json # machine-readable wiring
./netllm connect hermes-agent --toggle # optional: register routing.sources
```

Hermes does **not** use `OPENAI_BASE_URL` for custom endpoints — only `config.yaml` (or the `hermes model` wizard).

## Provider: `litellm` vs `custom`

| Provider | Use with netllm |
|----------|-----------------|
| **`litellm`** (recommended) | Speaks OpenAI `/v1/chat/completions` and `/v1/models`. Reliable chat and thinking passthrough. |
| **`custom`** (fallback only) | Hermes may probe Ollama-native paths (`/api/tags`, etc.) that netllm does not implement — blank chat or hangs. Use only if `litellm` fails for your Hermes version. |

## Core configuration (`~/.hermes/config.yaml`)

```yaml
model:
  default: "<model id from ./netllm models>"
  provider: litellm
  base_url: "http://127.0.0.1:11400/v1"
  api_key: "netllm-local"

display:
  show_reasoning: true
```

**Known source** (optional): use `api_key: "netllm-hermes-agent"` and register the source:

```bash
./netllm connect hermes-agent --toggle
```

Or add to `~/.config/netllm/config.toml`:

```toml
[[routing.sources]]
id = "hermes-agent"
description = "Hermes Agent CLI/TUI"
enabled = true
known_id = "hermes-agent"
```

Traffic from Hermes using `netllm-hermes-agent` appears separately in `GET /netllm/v1/status` `source_requests` and dashboard **Serving** stats.

### Fallback (`provider: custom`)

Only if `litellm` does not work:

```yaml
model:
  provider: custom
  base_url: "http://127.0.0.1:11400/v1"
```

If chat stays blank, switch back to `litellm`.

## Thinking / reasoning display

netllm preserves upstream `reasoning_content` on the OpenAI wire (streaming SSE and non-streaming). Hermes still needs:

1. A **thinking-capable model** on your backend (e.g. Qwen3 with thinking enabled).
2. **`display.show_reasoning: true`** in `config.yaml` (see core block above).
3. A recent Hermes Agent build — some versions had reasoning UI bugs upstream.

Manual smoke after netllm changes:

```bash
./netllm restart
./netllm connect hermes-agent
hermes chat
# or: hermes --tui
```

Why raw passthrough: the OpenAI Python SDK drops vendor extension fields when parsing responses; netllm’s adapter posts via raw HTTP for chat completions. See [sdk-versions.md](sdk-versions.md) change layers.

## Interactive setup (`hermes model`)

Outside any chat session:

```bash
hermes model
# → Custom endpoint (self-hosted / VLLM / etc.)
# → API base URL: http://127.0.0.1:11400/v1
# → API key: netllm-local (or netllm-hermes-agent)
# → Model name: <exact id from ./netllm models>
```

Inside a session, `/model` only switches among **already configured** providers — add netllm via `hermes model` first.

## CLI and TUI

Both use the same `model.*` block in `config.yaml`:

| Interface | Launch |
|-----------|--------|
| Classic CLI | `hermes` or `hermes chat` |
| TUI | `hermes --tui` or `display.interface: tui` in config.yaml |
| Messaging gateway | `hermes gateway start` (Telegram, Discord, Slack, etc.) |

TUI persistent default:

```yaml
display:
  interface: tui
```

Verify:

```bash
./netllm test --model <your-model>
hermes chat
# or: hermes --tui
```

## Docker and WSL

**Docker** (netllm on host, Hermes in a container):

```yaml
model:
  base_url: "http://host.docker.internal:11400/v1"
```

**WSL2** (model servers on Windows host): use the Windows host IP from WSL, not `localhost` — see [Hermes providers — Windows + WSL](https://hermes-agent.nousresearch.com/docs/integrations/providers).

## LAN / swarm gateway

Point Hermes at one gateway agent; netllm merges peer catalogs:

```bash
./netllm peers
```

```yaml
model:
  base_url: "http://<gateway-lan-ip>:11400/v1"
```

Model IDs stay the same; backends live only in netllm config on each mesh node.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Hermes cannot reach netllm | `./netllm serve` or `netllm start`; `./netllm doctor` |
| Model not found / 404 | Model string must match `./netllm models` exactly |
| Blank chat / no assistant text | Switch `provider` from `custom` to **`litellm`**; restart Hermes |
| Thinking not shown | `display.show_reasoning: true`; thinking model on backend; upgrade Hermes; `./netllm restart` after netllm update |
| TUI fails to start | `hermes doctor` — Node.js ≥ 20; try `hermes --cli` once |
| 401 from backend | Set API key on the backend in netllm Discovery/Servers tab or `[[routing.backends]]` |
| Wrong package | This guide is Hermes **Agent** (`hermes` CLI), not `pip install honcho` (Foreman clone) |

## See also

- [editor-integration.md](editor-integration.md) — shared client wiring
- [honcho-integration.md](honcho-integration.md) — Honcho memory platform (different product)
- Hermes: [Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers), [TUI](https://hermes-agent.nousresearch.com/docs/user-guide/tui)
- `./netllm sources list` — registry row for Hermes Agent when agent is running
