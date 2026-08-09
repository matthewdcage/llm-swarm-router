# Agent singleton hardening plan

Status: **Phase 1 complete** (2026-08-09). Finding **F-97** resolved.

Related: [agent-singleton-as-built.md](agent-singleton-as-built.md) ·
[agent-singleton-acceptance.md](agent-singleton-acceptance.md) ·
[architecture/10-audit-2026-08-08.md](../architecture/10-audit-2026-08-08.md) (F-97)

## Problem statement

Multiple netllm agent processes on one host cause:

1. **Port collisions** — second `netllm serve` on `:11400` fails or races the first.
2. **TOCTOU** — two concurrent `serve` calls can both pass `check_listen_port()` before
   either binds uvicorn.
3. **Orphan agents** — menubar quit or SIGTERM can leave uvicorn on the port while the
   supervisor thinks the agent stopped (documented in
   [solutions/macos-release-readiness.md](../solutions/macos-release-readiness.md)).
4. **systemd false-success** — `netllm serve -q` exits 0 when the same `agent_id` is
   already listening, so `Type=simple` sees the main process exit with no replacement child.
5. **mDNS collisions** — a draining predecessor keeps gossip/mDNS registered until process
   exit ([`stop_netllm_on_port`](../packages/netllm-discovery/src/netllm_discovery/runtime.py)
   escalates to SIGKILL for this reason).

Goal: **at most one live agent per config identity on a host** — detect on start, do not
spawn twins, replace only with explicit `--replace` (or supervisor default).

## Design principles

| Principle | Rationale |
|-----------|-----------|
| Port probe = reachability truth | `check_listen_port()` + `/health` stay authoritative for "is an agent serving?" |
| Flock = serialization | Closes the concurrent-start race; does not replace port checks |
| No new config knobs in Phase 1 | Lock path derived from existing state dir |
| Menubar unchanged | Bundled spawn already uses `serve -q --replace` |
| Replace is opt-in | Different `agent_id` on the same port never auto-killed without `--replace` |

## Lock file contract (Phase 1)

| Field | Value |
|-------|--------|
| **Path** | `{state_dir}/agent.lock` where `state_dir` = parent of `NetllmConfig.resolved_log_dir()` |
| **Linux example** | `~/.local/state/netllm/agent.lock` |
| **macOS example** | `~/Library/Application Support/netllm/agent.lock` |
| **Mechanism** | `fcntl.flock(LOCK_EX \| LOCK_NB)` on Unix; `msvcrt.locking` on Windows |
| **Payload** | JSON: `pid`, `agent_id`, `listen`, `started_at` (ISO-8601), `version` |
| **Hold duration** | From successful acquire until process exit (`atexit` + `serve` finally) |

### Stale recovery

1. **Dead PID in payload** — flock is already free (kernel releases on exit); acquirer
   truncates and writes fresh payload.
2. **Live PID, lock busy** — `AlreadyRunning`; `serve` exits 0 unless `--replace`.
3. **`--replace`** — `stop_netllm_on_port()` kills holder; flock releases; retry acquire.

## Phased roadmap

| Phase | Scope | Status |
|-------|--------|--------|
| **0** | `docs/dev-docs/` tree + index/DOX | **Done** (this PR) |
| **1** | `agent_lock.py`, `serve` integration, systemd `--replace`, doctor hint, tests | **Done** (2026-08-09) |
| **2** | `netllm doctor --fix-duplicates` | Planned |
| **3** | FastAPI lifespan lock verify (CLI bypass defense) | Planned |
| **4** | Windows service wrapper parity audit | Planned |
| **5** | Menubar lifecycle script lock assertions | Planned |

## Phase 1 behavior matrix

| Lock | Port (`check_listen_port`) | `--replace` | Action |
|------|---------------------------|-------------|--------|
| held, live other pid | any | no | exit 0 (already running) |
| held, live other pid | any | yes | stop holder → retry lock → continue |
| acquired | same `agent_id` | no | exit 0 |
| acquired | conflict, netllm | yes | `stop_netllm_on_port` → uvicorn |
| acquired | conflict | no | exit 1 with hints |
| acquired | free | any | uvicorn (hold lock) |

## PR slicing

| PR | Contents |
|----|----------|
| **This PR** | dev-docs + `agent_lock.py` + `serve` + systemd + doctor + tests + F-97 resolved |
| **Follow-up** | Phase 2 doctor `--fix-duplicates` only |
| **Follow-up** | Phase 3 agent lifespan (if needed after Phase 1 soak) |

## Out of scope (Phase 1)

- Auto-replace on different `agent_id` without `--replace`
- Dedup across multiple listen ports
- Changing menubar spawn arguments

## Exit gate (Phase 1)

- `./scripts/ci.sh lint` && `./scripts/ci.sh test` green
- Checklist in [agent-singleton-acceptance.md](agent-singleton-acceptance.md)
