# Editor integration reference

Base URL (local): `http://127.0.0.1:11400/v1`  
API key: `netllm-local` (any non-empty string works)

Always run `./netllm models` first and use an **exact** model ID from the table.

## Cursor

1. Ensure `./netllm serve` is running (`curl -sf http://127.0.0.1:11400/health`).
2. Open **Cursor Settings** → **Models**.
3. Add or override an **OpenAI-compatible** provider:
   - Base URL: `http://127.0.0.1:11400/v1`
   - API key: `netllm-local`
   - Model: copy from `./netllm models` (e.g. `llama3.2:latest`, `qwen2.5-coder:7b`)
4. Select that model in the chat/composer model picker.
5. Verify: `./netllm test --model <your-model>`

**Common failure:** model name in Cursor does not match backend — symptoms include empty responses or `model_not_found` in `./netllm test`.

## Claude Code

1. Export env in the shell that launches Claude Code (or add to `~/.zshrc`):
   ```bash
   export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
   export OPENAI_API_KEY=netllm-local
   ```
2. When using OpenAI-compatible routing in project config, point `base_url` at the same URL.
3. Model selection depends on Claude Code version — use a model ID from `./netllm models` where custom endpoints are supported.
4. Project context: [AGENTS.md](../../../AGENTS.md) and slash commands in `.claude/commands/`.

## Codex (OpenAI Codex CLI)

1. Shell exports (session or `~/.zshrc`):
   ```bash
   export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
   export OPENAI_API_KEY=netllm-local
   ```
2. Codex loads [AGENTS.md](../../../AGENTS.md) automatically from repo root.
3. Skills in `.agents/skills/` are discovered for setup/troubleshoot workflows.
4. Verify with `./netllm test` before relying on Codex for long tasks.

## VS Code + GitHub Copilot

When using an OpenAI-compatible custom endpoint (extension or org policy dependent):

- Base URL: `http://127.0.0.1:11400/v1`
- API key: `netllm-local`
- Model: from `./netllm models`

Copilot cloud models are separate — this path applies only when the user explicitly configures a compatible local endpoint.

## Honcho

See [docs/honcho-integration.md](../../../docs/honcho-integration.md).

Quick reference:

```toml
# Honcho deriver / dialectic overrides
base_url = "http://127.0.0.1:11400/v1"
api_key = "netllm-local"
```

Docker on host:

```toml
base_url = "http://host.docker.internal:11400/v1"
```

Swarm: point Honcho at the **gateway** machine URL only after `netllm gateway` on one host.

## LAN / swarm gateway

If using a gateway on another Mac:

```bash
./netllm peers
# use listen_url from gateway row
export OPENAI_BASE_URL=http://192.168.x.x:11400/v1
```

Run `./netllm models --url http://192.168.x.x:11400` to list routed models on that agent.
