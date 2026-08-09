# Agent singleton — as-built evidence map

Point-in-time map of duplicate-instance guards. Update when phases land.
Last updated: **Phase 1** (2026-08-09).

Plan: [agent-singleton-hardening-plan.md](agent-singleton-hardening-plan.md)

## CLI foreground start

| Location | Behavior |
|----------|----------|
| `packages/netllm-cli/src/netllm_cli/commands/serve_lifecycle.py` | `serve()` — lock acquire, port preflight, `--replace`, uvicorn |
| `packages/netllm-discovery/src/netllm_discovery/agent_lock.py` | **Phase 1** — flock lock file + JSON payload |
| `packages/netllm-discovery/src/netllm_discovery/runtime.py:55` | `check_listen_port()` — TCP + `/health` + `/status` |
| `packages/netllm-discovery/src/netllm_discovery/runtime.py:108` | `stop_netllm_on_port()` — SIGTERM → wait for pid exit → SIGKILL |
| `packages/netllm-discovery/src/netllm_discovery/process_util.py:80` | `port_owner_pid()` — lsof / ss / netstat |

### `serve()` decision order (post–Phase 1)

1. `acquire_agent_lock(cfg)` — serialize concurrent starts
2. `check_listen_port(cfg)` — same `agent_id` → exit 0; conflict → error or `--replace`
3. `uvicorn.run(...)` — hold lock until exit

## Background lifecycle

| Platform | Entry | Singleton notes |
|----------|-------|-----------------|
| Linux systemd | `packaging/linux/netllm.service` | **Phase 1:** `serve -q --replace` |
| macOS menubar | `apps/netllm-mac/Sources/Server/ServerProcess.swift:65` | `.alreadyRunning`, `.starting` claim, `serve -q --replace` in bundle |
| Windows service | `packages/netllm-cli/src/netllm_cli/lifecycle/windows.py` | `sc start/stop` — no flock-specific wrapper yet (Phase 4) |
| `netllm start` | `lifecycle/linux.py`, `lifecycle/darwin.py` | Delegates to systemd / menubar control socket |

## macOS supervisor (`ServerProcess`)

| Symbol | File:line | Role |
|--------|-----------|------|
| `StartResult.alreadyRunning` | `ServerProcess.swift:16` | Return when state is running/starting/unresponsive or port healthy |
| `start()` | `ServerProcess.swift:65` | Port health check before spawn; `.starting` anti-twin |
| `adoptHealthyListener()` | `ServerProcess.swift:362` | Orphan adopt on launch |
| `releaseListenPort()` | `ServerProcess.swift:373` | Calls `stop_netllm_on_port` via embedded Python |
| `doStart()` bundled args | `ServerProcess.swift:169` | `serve -q --replace --config …` |

## Doctor

| Location | Behavior |
|----------|----------|
| `packages/netllm-cli/src/netllm_cli/commands/diagnose.py:304` | `check_listen_port` issue + fix hints |
| **Phase 1** | Lock file path + holder pid when `agent.lock` present |

## FastAPI agent

| Location | Phase 1 |
|----------|---------|
| `packages/netllm-agent/src/netllm_agent/app.py` | No in-process lock (Phase 3 candidate) |

## Tests

| File | Covers |
|------|--------|
| `tests/test_runtime.py` | Port conflict formatting, `stop_netllm_on_port` SIGKILL escalation |
| `tests/test_agent_lock.py` | **Phase 1** — acquire, stale reclaim, serve integration |
| `tests/test_doctor_supervised_port.py` | Menubar-supervised port skip |

## Graphify communities

- **Agent Serve Lifecycle** — `serve()`, `check_listen_port()`, `stop_netllm_on_port()`
- **ServerProcess macOS** — `alreadyRunning`, `StartResult`

Run: `graphify query "serve alreadyRunning agent lock"` from repo root.
