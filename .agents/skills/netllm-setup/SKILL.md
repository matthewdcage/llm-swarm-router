---
name: netllm-setup
description: |
  First-time install and bootstrap of swarm-llm (netllm) from a repo checkout.
  Use when the user asks to install swarm-llm, set up netllm, get the router
  running, clone and configure llm-swarm-router, or invokes /netllm-setup.
  Runs uv sync, netllm init, discover, serve verification, and prints OpenAI
  client env vars. Requires macOS or Linux with uv and git.
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

# netllm setup

## When to use this skill

- First-time install from a git clone
- User says "set up netllm", "install swarm-llm", or `/netllm-setup`
- Agent needs to verify the router is healthy before editor integration

## Prerequisites

- macOS or Linux
- `git` on PATH
- `uv` on PATH — if missing, tell user to install from https://docs.astral.sh/uv/
- Repo root contains [pyproject.toml](pyproject.toml) with `name = "netllm"` and `[tool.uv.workspace]`
- At least one inference server (oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`) is optional but warn if none are online after discover

## Install paths

| User preference | Steps |
|-----------------|-------|
| **macOS DMG** | Download Release DMG → drag to Applications → launch menubar app → welcome wizard. See [docs/menubar-app.md](../../docs/menubar-app.md). |
| **Homebrew** | `brew tap matthewdcage/netllm <repo>` → `brew install netllm` → `brew services start netllm` |
| **Source / dev** | Workflow below (`uv sync`, `./netllm init`, `./netllm serve`) |

DMG and Homebrew use `netllm start` / `netllm stop` for background lifecycle. Source installs keep `./netllm serve` (foreground).

## Workflow

1. **Locate repo root** — `cd` to directory with `./netllm` wrapper and workspace `pyproject.toml`. If cwd is wrong, ask user for clone path.

2. **Check tools**
   ```bash
   command -v uv && command -v git
   ```

3. **Install dependencies**
   ```bash
   uv sync
   ```

4. **Initialize config** — run from repo root:
   ```bash
   ./netllm init
   ```
   If config already exists and user did not ask to overwrite, skip to step 5.
   Ask before `./netllm init --no-global-cli` if user wants repo-only CLI (no `~/.local/bin` install).

5. **Scan local providers**
   ```bash
   ./netllm discover
   ```
   Report online vs offline providers. If all offline, warn but continue — agent can still serve when backends come online.

6. **Start the agent** — `./netllm serve` is long-running. Either:
   - Run in background with logged output, or
   - Instruct user to run `./netllm serve` in a dedicated terminal and wait for "Starting netllm agent"

7. **Verify setup**
   ```bash
   scripts/agent-verify-setup.sh
   ```
   If script fails with "agent not running", ensure step 6 completed.

8. **Print client wiring** — show user:
   ```bash
   export OPENAI_BASE_URL=http://127.0.0.1:11400/v1
   export OPENAI_API_KEY=netllm-local
   ```

9. **Next step** — offer `/netllm-connect` or the `netllm-connect-editor` skill to wire Cursor, Claude Code, or Codex.

## Examples

**Goal:** Fresh clone on a Mac with Ollama running

```bash
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync
./netllm init
./netllm discover
./netllm serve   # dedicated terminal
# second terminal:
scripts/agent-verify-setup.sh
```

**Expected:** health check returns ok; `./netllm models` lists at least one model.

## Edge cases

| Situation | Action |
|-----------|--------|
| `uv` not found | Stop; link https://docs.astral.sh/uv/ |
| Config exists | Use `./netllm discover` instead of `--force` init |
| No providers online | Continue; tell user to start Ollama/oMLX and run `./netllm discover` |
| Port 11400 in use | `./netllm serve --port 11401` and adjust `OPENAI_BASE_URL` |
| Global CLI PATH confusion | Prefer `./netllm` from repo; run `./netllm doctor` |

## Do not

- Commit changes unless the user explicitly asks
- Edit user `.env` files without permission
- Assume `netllm` is on PATH outside the repo
