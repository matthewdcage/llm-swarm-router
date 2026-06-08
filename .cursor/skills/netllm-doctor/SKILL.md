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
   - Global install: `~/.local/bin/netllm`: run `./netllm env` for PATH export if needed

3. **Agent reachability**
   ```bash
   curl -sf http://127.0.0.1:11400/health || echo "agent down"
   ```
   If down, start by install channel:
   - **Source / dev:** `./netllm serve` (foreground)
   - **macOS app / Homebrew:** `./netllm start` or menubar → Restart Agent
   - **Linux deb/rpm:** `systemctl --user enable --now netllm`: see [docs/linux-install.md](../../docs/linux-install.md)
   - **Windows zip/winget:** `netllm start` after `install-service.ps1`: see [docs/windows-install.md](../../docs/windows-install.md)

4. **Local providers**
   ```bash
   ./netllm discover
   ```
   Expected ports: Ollama `11434`, LM Studio `1234`, vLLM `8000`; oMLX `8080` on **macOS only**

5. **Config review**, read `~/.config/netllm/config.toml` (or path from user):
   - `agent.listen`: loopback vs `0.0.0.0`
   - `agent.advertise`: required for gateway role
   - `swarm.mdns`: needs zeroconf from `uv sync`
   - `swarm.cluster_token`: should be set when on `0.0.0.0`

6. **Platform-specific swarm checks** (when `./netllm peers` is empty but LAN routing is expected):
   - **Linux:** mDNS uses Avahi via `python-zeroconf`; install Avahi if browse fails. Fallback: `swarm.peers` or `./netllm peers --subnet-scan --save`
   - **Windows:** mDNS is often blocked by firewall or missing Bonjour: prefer static `swarm.peers` or `--subnet-scan`. Allow inbound TCP on agent port (default `11400`) when `serve --host 0.0.0.0`
   - **All platforms:** Guest Wi‑Fi often blocks mDNS; loopback bind (`127.0.0.1`) hides the agent from LAN peers

7. **Inference test**
   ```bash
   ./netllm test
   ```

8. **Structured report**, for each issue: **Problem** → **Fix** → **Verify command**

9. **Re-run doctor** after fixes:
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
Fix: Start Ollama (ollama serve); on macOS also oMLX. Linux/Windows: Ollama, LM Studio, or vLLM, see install docs
Verify: ./netllm discover && ./netllm models
```

**Windows swarm: no peers**

```
Problem: ./netllm peers empty on Windows LAN
Fix: Add swarm.peers in config or ./netllm peers --subnet-scan --save; allow firewall inbound on :11400
Verify: ./netllm peers && ./netllm models --lan
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
| Doctor passes but editor fails | Run `netllm-connect-editor` skill: likely model name mismatch |
| Wrong netllm on PATH | `./netllm install` from repo or use `./netllm` only |
| Config missing | `./netllm init` |

## Do not

- Delete config without user approval: suggest `./netllm init --force` explicitly
- Commit diagnostic output containing secrets
