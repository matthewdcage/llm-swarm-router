# netllm-core

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Shared routing, backend health cache, configuration I/O, model catalog types, and Anthropic Messages bridge logic. No FastAPI, no vendor SDK imports.

## Ownership

Key modules: `config.py`, `routing_policy.py`, `pool.py`, `health.py`, `models.py`, `capabilities.py`, `anthropic_bridge.py`, `update.py`, `platform.py`, `cloud_providers.py`.

## Local Contracts

- Config path: `~/.config/netllm/config.toml` (see root `config.example.toml`)
- `anthropic_bridge.py` translates Messages API ↔ OpenAI-compatible backends; SDK calls stay in `netllm-sdk-anthropic`
- Routing strategies and backend health drive agent and CLI behavior
- **`ensure_lan_mesh_defaults()`** / **`is_lan_listen()`** in `models.py`: LAN bind → `local_spillover` + `subnet_scan` without minting `cluster_token`; called from CLI `serve`, config JSON import, and menubar save. The strategy upgrade is **one-shot** (`routing.lan_defaults_applied`) — an explicit user strategy choice is never rewritten after the first upgrade (mirrored in Swift `applyLanMeshDefaults`)
- **Per-request routing headers** (constants in `models.py`, resolved in `routing_policy.resolve_routing`): `x-netllm-strategy` (one-off strategy override), `x-netllm-backend` (pin to backend id / `peer:<agent-id>` / base URL → `pool.backend_by_id`), `x-netllm-hops` (agent-hop counter; ≥ `MAX_FORWARD_HOPS` forces local — backstop beside `x-netllm-local-only`)
- **Health knobs are config-driven** (`routing.health_ttl_s`, `offline_retry_s`, `max_backend_failures`): offline entries re-probe after the shorter `offline_retry_s` window, and a failed probe keeps the last known model catalog (never wipe `health.models` to `[]`)
- **`prune_peer_rows(keep_urls)`**: callers merging peers must follow with a prune so pool rows track the swarm registry (dead peers must not linger)
- **`local_spillover`** (swarm default from guided init / LAN mode): serve locally below `routing.spillover_max_local_in_flight` concurrent requests, spill to the least-loaded peer above it; peer load = heartbeat-reported local rows + own active hops (`RouterPool._own_peer_hops` ledger — use `pool.acquire()`/`pool.release()`, never mutate `in_flight` directly)
- **`merge_backends` keeps local Backend object identity** (updates fields in place): in-flight requests hold a reference across refreshes, so replacing the row would leak `in_flight` counts — peer rows are still rebuilt from heartbeats + hop ledger
- **Model matching is case-insensitive** (`model_names_for` alias keys, `_serves_model`); `capabilities.model_capability()` classifies model IDs (`chat`/`embedding`/`audio`/`rerank`/`other`, unknown defaults to `chat`) — agent uses it to reject chat against encoders and to filter `known_models(capability=…)`
- **`select_backend(exclude_ids=…)`**: retry loops pass the per-request failed-backend set so the attempt budget walks on to untried candidates (e.g. a healthy LAN peer) instead of re-hitting a failing local backend
- **Capacity vs hard failures** (`is_capacity_error`, `mark_failure(capacity=…)`): 409/429/503/507 and capacity markers in wrapped bodies (`prefill_memory_exceeded`, "is busy", "memory pressure", "rate limit" — peer agents wrap upstream refusals in 502, so match the message too) mean "full now, not broken" — they must never count toward the offline trip; tracked in `pool.capacity_rejections`
- **`max_in_flight_per_backend`** (config `routing.max_in_flight_per_backend`, 0 = off): every strategy prefers candidates under the cap; all-at-cap falls through to normal selection (never fail a request because of the cap)
- **`"auto"` strategy**: agent maps shard-context requests to batch_shard before the pool; in the pool `auto` resolves to least_load
- **Peer-row health hydration**: `merge_backends` copies the health cache verdict onto rebuilt peer rows — status display must never diverge from the cache's routing truth (`plan_batch_shard`/`BatchShardPlan` are gone; `routing.require_same_model_for_shard` is a kept-for-compat no-op)
- **`cloud_providers.py`**: code-owned registry (base URLs, auth modes, model catalogs) for the five pre-configured providers — not user config, never persisted. `CloudConfig`/`CloudProviderConfig` in `models.py` hold the user-facing `[cloud]` section; absent section == `CloudConfig()` defaults (enabled=True, fallback="cloud") reproduce pre-cloud-feature behavior exactly. See [docs/cloud-providers-plan.md](../../docs/cloud-providers-plan.md).
- **`Backend.cloud_provider`/`auth_mode`**: tags on materialized cloud rows (`netllm-agent`'s `_materialize_cloud_provider_backends`) — `cloud_provider` names the registry id (drives `pool.select_backend(prefer_cloud=…)` and pruning), `auth_mode` ("api_key" default, "bearer" for Anthropic `plan_token`) picks the upstream SDK auth kwarg
- **`resolve_routing(..., cloud=…)`**: `cloud.enabled=False` hard-disables cloud regardless of policy; `cloud.fallback="none"` suppresses the *default* cloud-allowed stance but an explicit `allow_cloud` policy still opts a route in; `cloud.fallback="local"` sets `cloud_leads=True` (cloud tried before local/peer mesh)

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
