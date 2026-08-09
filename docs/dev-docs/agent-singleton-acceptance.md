# Agent singleton — acceptance checklist

Gates for [agent-singleton-hardening-plan.md](agent-singleton-hardening-plan.md) Phase 1.
Pattern follows [solutions/macos-release-readiness.md](../solutions/macos-release-readiness.md).

## Automated (CI)

| Gate | Command |
|------|---------|
| Lint | `./scripts/ci.sh lint` |
| Tests | `./scripts/ci.sh test` |
| Lock unit tests | `uv run pytest tests/test_agent_lock.py -q` |
| Runtime regression | `uv run pytest tests/test_runtime.py -q` |

## Linux / source install

| # | Step | Expected |
|---|------|----------|
| L1 | Terminal A: `./netllm serve` | Agent listens on configured port |
| L2 | Terminal B: `./netllm serve` | Exit **0**, message "already running", **one** PID on port |
| L3 | `./netllm serve --replace` | Old PID gone, new agent serves `/health` |
| L4 | `ls ~/.local/state/netllm/agent-*-11400.lock` | JSON with current `pid`, `agent_id`, `listen` |
| L5 | `systemctl --user restart netllm` (packaged) | Single PID after restart; unit stays active |
| L6 | `./netllm doctor` with port in use | Issue cites port + lock path when lock exists |

## macOS menubar

| # | Step | Expected |
|---|------|----------|
| M1 | Menubar **Start** with no agent | One PID on `:11400` |
| M2 | Menubar **Start** again | `already_running`; no second child |
| M3 | `./netllm serve` while menubar agent runs | Exit 0 or replace per menubar policy; no twin |
| M4 | Quit app with **Stop** | Port freed; lock released (file may remain with stale pid until next start reclaims) |
| M5 | `scripts/test-menubar-lifecycle.sh` (when Stage `.app` exists) | Existing L5 `serve --replace` gate still passes |

## Windows (Phase 1 — lock best-effort)

| # | Step | Expected |
|---|------|----------|
| W1 | `netllm serve` × 2 | Second instance exits 0 or port conflict; no twin listeners |
| W2 | Lock file under `%LOCALAPPDATA%\\netllm\\agent-*-<port>.lock` | Present when agent runs |

## Regression watchlist

- mDNS name collision retry (`tests/test_runtime.py::test_mdns_advertiser_retries_after_collision`) — unchanged
- Menubar `serve -q --replace` bundle path — unchanged spawn args
- `stop_netllm_on_port` waits for **process exit**, not just port free

## Sign-off

Phase 1 complete when CI green and at least **L1–L4** + **M1–M2** verified on maintainer hardware.
