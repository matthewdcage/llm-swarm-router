---
name: netllm-doctor
description: |
  Troubleshoot swarm-llm (netllm) misconfigurations. Use when netllm is broken,
  no models appear, the agent is unreachable, mDNS fails, PATH is wrong, or
  the user invokes /netllm-doctor. Runs netllm doctor and structured checks
  for providers, listen address, and global vs repo-local CLI.
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

# netllm doctor

## When to use this skill

- Setup failed or agent unreachable
- No models in `./netllm models`
- Swarm peers not discovered
- User says "netllm broken", "fix netllm", or `/netllm-doctor`

## Workflow

1. **Run built-in doctor**
   ```bash
   ./netllm doctor
   ```
   Capture each issue title and suggested fix from output.

2. **PATH check**
   ```bash
   which netllm || true
   ./netllm --version
   ```
   - Repo checkout: prefer `./netllm` from root
   - Global install: `~/.local/bin/netllm` — run `./netllm env` for PATH export if needed

3. **Agent reachability**
   ```bash
   curl -sf http://127.0.0.1:11400/health || echo "agent down"
   ```
   If down: instruct `./netllm serve` (or check wrong port in config)

4. **Local providers**
   ```bash
   ./netllm discover
   ```
   Expected ports: oMLX `8080`, Ollama `11434`, LM Studio `1234`

5. **Config review** — read `~/.config/netllm/config.toml` (or path from user):
   - `agent.listen` — loopback vs `0.0.0.0`
   - `agent.advertise` — required for gateway role
   - `swarm.mdns` — needs zeroconf from `uv sync`
   - `swarm.cluster_token` — should be set when on `0.0.0.0`

6. **Inference test**
   ```bash
   ./netllm test
   ```

7. **Structured report** — for each issue: **Problem** → **Fix** → **Verify command**

8. **Re-run doctor** after fixes:
   ```bash
   ./netllm doctor && scripts/agent-verify-setup.sh
   ```

## Examples

**Agent unreachable**

```
Problem: curl /health fails
Fix: ./netllm serve in dedicated terminal
Verify: curl -sf http://127.0.0.1:11400/health
```

**No providers online**

```
Problem: discover shows 0/3 online
Fix: Start Ollama (ollama serve) or oMLX
Verify: ./netllm discover && ./netllm models
```

**mDNS unavailable**

```
Problem: doctor reports zeroconf missing
Fix: uv sync from repo root
Verify: ./netllm peers
```

## Edge cases

| Situation | Action |
|-----------|--------|
| Doctor passes but editor fails | Run `netllm-connect-editor` skill — likely model name mismatch |
| Wrong netllm on PATH | `./netllm install` from repo or use `./netllm` only |
| Config missing | `./netllm init` |

## Do not

- Delete config without user approval — suggest `./netllm init --force` explicitly
- Commit diagnostic output containing secrets
