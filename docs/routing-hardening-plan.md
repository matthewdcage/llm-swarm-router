# Routing audit, hardening plan & status

Audited 2026-07-20 (core routing, discovery/swarm, config, CLI + macOS app +
dashboard, tests). This document records why swarm routing "stopped" sending
calls to detected peers, what was fixed, and what remains.

## Root causes of "peers detected but calls stay local"

1. **Strategy semantics.** `local_first` never uses peers while any local
   backend exists. `local_spillover` (auto-applied on LAN binds) only spills
   when the local backend has ≥ `spillover_max_local_in_flight` (default 2)
   concurrent requests **and** a peer is strictly less loaded — sequential
   traffic therefore always stays local. Early on (local model still
   loading/unhealthy) requests spilled; once local became healthy, routing
   correctly went 100% local. This is policy, not a fault — but it was
   undocumented and un-overridable per request.
2. **Anthropic Messages path ignored strategy.** `/v1/messages` used a
   bespoke candidate ordering that only honored `local_first`/`local_spillover`;
   `round_robin`, `least_load`, `latency_weighted`, `batch_shard` did nothing.
   Clients like Claude Code always hit the local backend.
3. **Strategy choices were silently discarded.** `ensure_lan_mesh_defaults`
   (Python, on every load) and `applyLanMeshDefaults` (Swift, on every save)
   rewrote `local_first` → `local_spillover` on LAN binds, overriding explicit
   user choice each time.
4. **Peers could drop out permanently.** Peer records pruned after 45 s
   without a heartbeat; mDNS discovery is edge-triggered and the subnet scan
   was one-shot at startup — a sleep/Wi-Fi blip removed a peer forever.
   Separately, pruned peers' pool rows were *never* removed, and 3 transient
   failures blackholed a peer until a 30 s TTL expired.
5. **No way to target a peer.** UIs show peers but nothing routed *to* a
   chosen peer; there was no per-request strategy or backend selector.
6. **Config edits didn't reach the live router.** The dashboard hot-applied
   only `default_strategy`; pool-affecting edits (peers, backends,
   `allow_remote`, thresholds) reported "saved" but needed a restart. macOS
   app saves also **wiped** every field its Swift structs don't model
   (`model_aliases`, `spillover_max_local_in_flight`, `provider_urls`, …)
   because `config import` replaced the file wholesale.

## Implemented (this pass)

Routing correctness

- `/v1/messages` (and streaming) now use the same strategy-driven selection
  loop as chat completions; anthropic-format backends (cloud) remain a final
  fallback tier so they never shadow the local mesh.
- Per-request override headers, honored on all proxy routes:
  - `x-netllm-strategy: round_robin` — one-off strategy override
  - `x-netllm-backend: peer:<agent-id> | <backend-id> | <base-url>` — pin
  - `x-netllm-local-only: 1` — unchanged
- `x-netllm-hops` counter on agent→agent forwards; requests with hops ≥ 2 are
  forced local (backstop loop guard alongside the local-only header).
- One-shot LAN defaults: `routing.lan_defaults_applied` marks the upgrade;
  an explicit `local_first` choice is never rewritten again (Python + Swift).

Peer durability

- Pool prunes peer rows the registry no longer tracks (and their hop-ledger
  entries).
- `swarm.rediscover_interval_s` (default 60 s) background loop re-probes
  previously seen peer URLs and re-runs the subnet scan when the registry is
  empty — peers lost to sleep/Wi-Fi blips rejoin without a restart.
- `swarm.peer_stale_after_s` (default 45 s) replaces the hardcoded prune age.
- Offline backends re-probe after `routing.offline_retry_s` (default 10 s)
  instead of waiting out the full 30 s health TTL; failed probes keep the
  last known model catalog instead of wiping it.
- Health knobs are configurable: `routing.health_ttl_s`,
  `routing.offline_retry_s`, `routing.max_backend_failures`.

Config integrity & hot-apply

- `POST /netllm/v1/admin/config` now calls `service.apply_config()` — pool
  knobs, aliases, `allow_remote`, swarm settings re-sync live, and the
  provider-scan cache is invalidated (dashboard edits take effect without
  restart).
- `netllm config import` deep-merges over the on-disk config instead of
  replacing it — macOS app saves no longer wipe unmodeled fields.
- Validation: `spillover_max_local_in_flight ≥ 1`, `heartbeat_interval_s > 0`,
  health knobs positive (pydantic `Field` constraints).
- `require_same_model_for_shard` is now actually wired into
  `plan_batch_shard` (was a fully-plumbed no-op toggle).

Hardening

- Cluster-token comparisons use `secrets.compare_digest` (heartbeat + admin).
- `config.toml` written with mode 0600 (holds tokens/API keys).
- Mid-stream failover disabled after the first chunk reaches the client
  (an SSE error event ends the stream instead of replaying a response).
- mDNS peer-callback tasks are retained (were GC-able mid-flight).
- `BatchRequestLedger` bounded (oldest-half eviction at 8192 entries).
- A bare OpenAI `user` field no longer becomes a shard key; only the
  explicit `netllm:<batch>:<index>` convention opts in.
- Dead `SwarmRegistry.peer_backends()` wrapper removed.

Tests: `tests/test_routing_hardening.py` covers all of the above;
`test_messages_api_round_robin_reaches_peer` locks in the Messages-path fix.

## Remaining phases (not yet implemented)

Phase 2 — consolidation

- Serve config schema/defaults from the agent so `dashboard.js` and the Swift
  structs stop hand-mirroring the pydantic models (drift already happened).
- macOS app: use the admin API for config/discover instead of CLI shell-outs.
- Remove dead code: ~~`batch.py` (`run_batch_shard` has no callers)~~,
  ~~`is_chat_capable`~~, ~~`openai_error_to_anthropic`~~, duplicate mDNS browse in
  `lan.py`, ~~duplicated oMLX scoring in `local.py`~~; either use or remove
  `GET /netllm/v1/peers` and `/backends`.
- Collapse the three LAN-defaults call sites onto `ensure_lan_mesh_defaults`
  (partial: `netllm join` and open-swarm init now call `ensure_lan_mesh_defaults`
  instead of duplicating strategy/subnet fields).

Phase 3 — durability/security

- Async health probes with shared, reused HTTP clients per backend (today a
  new SDK client is built per attempt; sync probes can stall worker threads
  5 s per unreachable peer).
- Optional token enforcement on `/v1/*` for LAN binds.
- `agent.listen` parse/validation at load (IPv6-safe) with friendly errors.
- UI surfacing: per-peer "routed calls" counters in status/dashboard so
  "discovered but idle" is visible at a glance.

Phase 4 — future feature: shared model lists & batch preferences

User goal: machines with overlapping-but-not-identical catalogs share calls
and batches according to saved, editable preferences.

Sketch:

- New `[routing.model_groups]` config: named groups mapping a canonical model
  to per-host allow/deny and weight, e.g.

  ```toml
  [[routing.model_groups]]
  name = "chat-large"
  models = ["llama3:70b", "Meta-Llama-3-70B"]
  prefer = ["mac-studio"]        # agent_ids / hostnames, in order
  weights = { mac-studio = 2, macbook = 1 }
  batch = true                    # eligible for batch_shard
  ```

- Selection: groups resolve like model_aliases today, then weight the
  candidate pool (weighted round-robin / least-load within the group).
- Preferences editable in the dashboard + macOS Settings ("Models" tab:
  per-model toggle per peer, weight slider), persisted via the merge-safe
  admin config path added in this pass.
- Batch planner uses group membership instead of "same first model on every
  backend", generalizing `require_same_model_for_shard`.

## Verifying on your two machines

1. Update both machines, restart agents.
2. Sequential test through any strategy:
   `curl -X POST http://127.0.0.1:11400/v1/chat/completions -H 'x-netllm-strategy: round_robin' ...`
   — alternates local/peer.
3. Pin the peer: `-H 'x-netllm-backend: peer:<agent-id>'` (agent id from
   `netllm status` / `/netllm/v1/status`).
4. Claude Code (Messages API) now follows `default_strategy` — set
   `round_robin` to spread, or keep `local_spillover` for busy-only spill.
5. Sleep/wake the second machine: it should reappear within
   `rediscover_interval_s` (60 s default) without restarting anything.
