---
name: netllm-connect-editor
description: |
  Wire AI coding tools (Cursor, Claude Code, Codex, VS Code Copilot, Honcho)
  to a running netllm agent at http://127.0.0.1:11400/v1. Use when the user
  asks to connect Cursor to netllm, use local LLM in Claude Code or Codex,
  point an editor at the swarm router, or invokes /netllm-connect. Requires
  netllm serve running and a model name from ./netllm models.
version: 1.0.0
license: MIT
compatibility:
  - cursor
  - codex
  - claude-code
  - copilot
allowed-tools:
  - Read
  - Shell
  - Grep
---

# netllm connect editor

## When to use this skill

- User wants Cursor, Claude Code, Codex, or Honcho to use local models via netllm
- After `/netllm-setup` or when agent is already running
- User invokes `/netllm-connect`

## Prerequisites

- netllm agent running: `curl -sf http://127.0.0.1:11400/health`
- Model ID from `./netllm models` (exact string required in most editors)

Load detailed per-editor steps from [references/editor-settings.md](references/editor-settings.md) when needed.

## Workflow

1. **Confirm agent is up**
   ```bash
   curl -sf http://127.0.0.1:11400/health && echo ok
   ./netllm models
   ```
   Pick a model ID from output; note it for the user.

2. **Ask which editor** — Cursor, Claude Code, Codex, VS Code Copilot, or Honcho. If user already named one, skip.

3. **Apply editor-specific config** — follow [references/editor-settings.md](references/editor-settings.md). Never auto-edit `settings.json` without explicit user consent; show copy-paste instructions instead.

4. **Set shell env** — pick one API surface:

   OpenAI-compatible (Cursor, Codex, Copilot):
   ```bash
   export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
   export OPENAI_API_KEY=netllm-local
   ```

   Native Anthropic Messages API (Claude Code):
   ```bash
   export ANTHROPIC_BASE_URL=http://127.0.0.1:11400
   export ANTHROPIC_API_KEY=netllm-local
   ```

   For LAN gateway, replace host with gateway IP (e.g. `http://192.168.1.10:11400`).

5. **Verify inference**
   ```bash
   ./netllm test --model <id>              # OpenAI path
   ./netllm test --api anthropic --model <id>  # Anthropic path
   ```

6. **Report** — editor, base URL, model name, verification result. If `model_not_found`, model string does not match backend — re-run `./netllm models`.

## Examples

**Goal:** Cursor on same machine as netllm

1. `./netllm models` → e.g. `llama3.2:latest`
2. Cursor Settings → Models → enable OpenAI-compatible override
3. Base URL: `http://127.0.0.1:11400/v1`, API key: `netllm-local`, model: `llama3.2:latest`
4. `./netllm test --model llama3.2:latest`

## Edge cases

| Situation | Action |
|-----------|--------|
| Agent not running | Run `netllm-setup` skill or `./netllm serve` |
| Empty model list | Start Ollama/oMLX; `./netllm discover`; restart serve |
| Dockerized Honcho | Use `http://host.docker.internal:11400/v1` — see [docs/honcho-integration.md](../../../docs/honcho-integration.md) |
| Remote swarm gateway | Point client at gateway URL from `./netllm peers` |

## Do not

- Modify user editor settings files without explicit permission
- Use cloud API keys when routing through netllm — `netllm-local` is sufficient
