# Windows troubleshooting — netllm

Install guide: [windows-install.md](windows-install.md) · Overview: [platform-matrix.md](platform-matrix.md)

## First steps (all issues)

```powershell
netllm doctor
curl -sf http://127.0.0.1:11400/health
netllm status
```

Optional UI: http://127.0.0.1:11400/ui/ (source/latest agent) · else `netllm status`.

From a repo checkout, use `.\netllm` in PowerShell from the project root.

---

## Agent unreachable

| Symptom | Fix |
|---------|-----|
| `curl` to `/health` fails | **Service install:** `netllm start` (after `install-service.ps1` as Admin) |
| Service won’t start | Run `sc query NetllmAgent` — re-run `install-service.ps1` from extract folder |
| **Source install** | `.\netllm serve` in a terminal |
| Port 11400 in use | `netstat -ano \| findstr 11400` — stop duplicate with `netllm stop` |

Logs: `%LOCALAPPDATA%\netllm\logs\agent.log`.

**Verify:** `curl -sf http://127.0.0.1:11400/health`.

---

## `netllm` not recognized

| Symptom | Fix |
|---------|-----|
| Command not found after zip install | Open a **new** terminal — `install-service.ps1` adds `python\Scripts` to user PATH |
| Still missing | Use full path: `<extract-dir>\netllm.cmd` |
| **Source** | `.\netllm` from repo or `uv tool install` via `.\netllm install` |

---

## No models / backends offline

| Symptom | Fix |
|---------|-----|
| `netllm models` empty | Start **Ollama**, **LM Studio**, or **vLLM** on Windows (or WSL — see below) |
| Discovery missed server | `netllm discover` (or dashboard **Discover providers** on source/latest builds) |

Default probes: Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`. oMLX is not available on Windows.

**Verify:** `netllm discover` then `netllm models`.

---

## WSL vs native Windows

| Setup | Rule |
|-------|------|
| **Inference in WSL2** | Run `netllm serve` in the **same WSL distro** so `127.0.0.1` probes reach Ollama/vLLM |
| **Inference native** | Run Ollama/LM Studio for Windows; run `netllm` on native Windows |
| **Mixed** | Do not expect Windows netllm to discover servers only listening inside WSL unless ports are forwarded |

---

## Swarm / LAN peers

| Symptom | Fix |
|---------|-----|
| `netllm peers` empty | Windows firewalls often block mDNS — use static `swarm.peers` or `netllm peers --subnet-scan --save` |
| LAN routing | `netllm serve --host 0.0.0.0` and allow **inbound TCP on port 11400** in Windows Firewall |
| Cluster security | Set `swarm.cluster_token` when listening on `0.0.0.0` |

**Verify:** `netllm peers` and `netllm models --lan`.

---

## Service / Admin issues

- `install-service.ps1` requires **Administrator** PowerShell.
- After moving the extract folder, re-run `install-service.ps1` — the service points at the original `netllm.exe` path.

---

## Editor connects but requests fail

1. Export `OPENAI_BASE_URL=http://127.0.0.1:11400/v1`, `OPENAI_API_KEY=netllm-local`, and matching `ANTHROPIC_*` vars.
2. Match model IDs from `netllm models`.
3. [editor-integration.md](editor-integration.md).

---

## Still stuck?

```powershell
netllm doctor
netllm test
```

Maintainer rehearsal: `.\scripts\emulate-user-install-windows.ps1` (Admin for service step).

Bug reports: [GitHub issues](https://github.com/matthewdcage/llm-swarm-router/issues/new?template=bug_report.yml).

Other platforms: [macos-troubleshooting.md](macos-troubleshooting.md) · [linux-troubleshooting.md](linux-troubleshooting.md)
