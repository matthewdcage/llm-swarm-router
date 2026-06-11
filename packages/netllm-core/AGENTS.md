# netllm-core

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Shared routing, backend health cache, configuration I/O, model catalog types, and Anthropic Messages bridge logic. No FastAPI, no vendor SDK imports.

## Ownership

Key modules: `config.py`, `routing_policy.py`, `pool.py`, `health.py`, `models.py`, `anthropic_bridge.py`, `batch.py`, `update.py`, `platform.py`.

## Local Contracts

- Config path: `~/.config/netllm/config.toml` (see root `config.example.toml`)
- `anthropic_bridge.py` translates Messages API ↔ OpenAI-compatible backends; SDK calls stay in `netllm-sdk-anthropic`
- Routing strategies and backend health drive agent and CLI behavior
- **`local_spillover`** (swarm default from guided init): serve locally below `routing.spillover_max_local_in_flight` concurrent requests, spill to the least-loaded peer above it; peer load = heartbeat-reported local rows + own active hops (`RouterPool._own_peer_hops` ledger — use `pool.acquire()`/`pool.release()`, never mutate `in_flight` directly)

## Work Guidance

- Keep pydantic models in `models.py`; avoid circular imports with agent/discovery
- Platform helpers in `platform.py` and `install_detect.py` are shared with CLI and agent
- `platform.local_admin_client_hosts()` — loopback plus this machine's interface IPs; used by agent admin gate so `http://<LAN-IP>:11400/ui/` on the same host works like `127.0.0.1`

## Verification

```bash
./scripts/ci.sh test   # tests/ cover routing and bridge
```

## Child DOX Index

None — flat `src/netllm_core/` package.
