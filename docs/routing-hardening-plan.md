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

Phase 2 — consolidation (done 2026-07-20)

- ~~Remove dead code~~: `batch.py` + `BatchDedupLedger` + `healthy_backends`
  (no production callers), `is_chat_capable`, `openai_error_to_anthropic`,
  `SwarmRegistry.peer_backends()` all removed; oMLX best-backend scoring
  deduped into `_best_omlx_base_url`; mDNS ServiceInfo decoding deduped into
  `mdns.decode_service_info` (used by both the background browser and the
  CLI's synchronous browse); redundant `except (ConnectError, Timeout,
  Exception)` tuples in `health.py` reduced to `except Exception`.
- ~~Collapse the three LAN-defaults call sites~~: `netllm join` and
  open-swarm init now call `ensure_lan_mesh_defaults` instead of duplicating
  strategy/subnet fields.
- ~~Schema-drift blast radius~~: `config import` deep-merges (phase 1), so
  UI structs no longer destroy unmodeled fields; `config_summary` now exposes
  all routing/swarm knobs (health TTLs, re-discovery, stale window,
  `lan_defaults_applied`) so the dashboard reads true state.
- Decision: `GET /netllm/v1/peers` and `/backends` are **kept** as read-only
  debug endpoints (cheap, useful for scripting/diagnosis) — documented here
  rather than removed.
- Deferred to Phase 3: serving a config schema from the agent (full fix for
  dashboard.js/Swift hand-mirroring) and moving the macOS app onto the admin
  API instead of CLI shell-outs.

Phase 3 — durability/security (done 2026-07-20)

- ~~Reused HTTP clients~~: upstream SDK clients are cached per
  (base_url, api_key, forward-headers) in `AgentService._openai_upstream`
  (no more per-attempt client construction), and sync health probes share
  one pooled `httpx.Client` (`health._shared_sync_client`). A full
  async-probe rewrite remains optional future work — probes are still
  sync but now connection-pooled and thread-offloaded.
- ~~Optional token enforcement on `/v1/*`~~:
  `swarm.require_token_for_inference = true` (with a `cluster_token`)
  gates inference for non-local clients; local clients are exempt and
  peer agents automatically forward with the cluster token
  (`_upstream_api_key`).
- ~~`agent.listen` validation~~: pydantic validator rejects malformed
  host:port (IPv6-bracket aware) at load; the CLI now prints a friendly
  "Config is invalid" error instead of a raw traceback.
- ~~Per-backend routed-call counters~~: `pool.routed_counts` (successful
  requests per backend id) is exposed as `routed_requests` in
  `/netllm/v1/status` — "peer discovered but idle" is now directly
  observable. Dashboard/menubar visualization of the counter is UI
  polish left for a future pass.

Still open (deferred): agent-served config schema for dashboard.js/Swift,
macOS app migration to the admin API, full async health probes.

Phase 4 — future feature: shared model lists & batch preferences

Done (2026-07-22), the simpler half of this: `[routing.model_pools.<name>]`
(`netllm_core.models.ModelPool`) lets named hosts accept *any* requested
model name — bypassing `model_aliases` matching entirely — as long as the
host serves one of the pool's allowed `models`. No weighting, no
`prefer`/`batch` fields, no dashboard UI yet; config-only. See
`config.example.toml` and the README feature table. `RouterPool.
backends_for_model` folds pool-eligible backends into the normal candidate
set (`pool_models_for_backend`), and `AgentService._model_for_backend`
falls back to `RouterPool.resolve_via_pool` to pick the actual upstream
model once alias resolution comes up empty.

Still future work (not built): the weighted/preferred variant below —
`prefer` ordering, per-host `weights`, and `batch` eligibility for the
shard planner. This is a strict superset of `model_pools`, not a separate
mechanism; whoever builds it should fold `model_pools` into `model_groups`
rather than keep two config sections doing overlapping things long-term.

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

Phase 5 — mesh utilization hardening (done 2026-07-21)

Field diagnosis (Mac mini gateway + MacBook Pro peer, both 0.4.1.0): the
mini stacked 4–5 requests locally while the MBP sat idle in bursts.
Observed causes, in order of impact:

1. **Degenerate strategy.** `default_strategy = "batch_shard"` with no
   client sending shard context fell back to round_robin on 100% of
   requests (wall-to-wall log warnings) — not load-aware, and
   `spillover_max_local_in_flight` was silently ignored.
2. **Capacity errors tripped the peer offline.** The MBP's oMLX
   rejected requests with 409 "cannot reload runtime settings variant
   until active requests finish" (wrapped as 502 by the peer agent);
   3 such rejections benched the peer for `offline_retry_s` windows
   while local work piled up. Local oMLX memory-guard 400s
   (`prefill_memory_exceeded`) burned retry attempts the same way.
3. **Status lied about peers.** Peer rows are rebuilt from heartbeats
   on every refresh with default health, so `/netllm/v1/status`
   permanently reported healthy peers as `unknown` with
   `model_count=0` (the real gate lives in the pool's health cache) —
   which misdirected the initial diagnosis toward "model discovery is
   broken" when discovery was in fact working.
4. **Silent config drift.** The two machines ran different strategies
   for an unknown period; nothing surfaced it.

Implemented:

- **Capacity-error classification** (`pool.is_capacity_error`): HTTP
  409/429/503/507 and known capacity markers in wrapped bodies
  (`prefill_memory_exceeded`, "memory pressure", "is busy", "rate
  limit") no longer count toward the offline trip. The backend is
  excluded for that request only and `pool.capacity_rejections` (per
  backend id) is exposed in `/netllm/v1/status`.
- **`routing.max_in_flight_per_backend`** (default 0 = off):
  back-pressure guardrail applied by *every* strategy — selection
  prefers backends under the cap; when all are at the cap it falls
  through rather than failing.
- **`default_strategy = "auto"`**: shard-context requests route via
  batch_shard; everything else balances by live in-flight load
  (least_load). Recommended for mixed interactive traffic.
- **Shardless-fallback telemetry**: batch_shard-without-context now
  logs once (then every 100th) and exports `shardless_fallbacks` in
  status, instead of spamming one warning per request.
- **Peer-row health hydration**: `merge_backends` copies the health
  cache's verdict (online/offline + last_check) onto rebuilt peer rows,
  and peer rows carry `model_count`; status now reports what routing
  actually believes.
- **Config-drift warnings**: heartbeats/status already carried
  `routing_strategy`; peers now record it plus `version`, and
  mismatches surface as `peer_warnings` in status and notes in the
  doctor payload.
- **Dead code removed**: `plan_batch_shard` + `BatchShardPlan` (no
  production callers; the live paths are the `BatchRequestLedger` and
  the hash branch of `select_backend`).
  `routing.require_same_model_for_shard` is therefore a no-op again —
  the field is kept so existing configs load, and its semantics move to
  the Phase 4 model_groups design.

Deferred (pending evidence): normalizing per-request runtime-settings
fields on peer forwards — first confirm which field makes oMLX reload a
"runtime settings variant"; if it is oMLX config drift between machines
(likely), aligning the two servers' model settings is the real fix.
Also deferred: collapsing the two-layer strategy-fallback dispatch
(`service._select_backend_for_request` vs `pool.select_backend`) — the
retry semantics should live in one layer.

Phase 6 — mesh fairness, scan safety & config-conflict prevention (done 2026-07-21)

Field diagnosis round 2 (post phase-5 rollout): the MBP peer still got
only sporadic traffic, and a chat model was being loaded under memory
pressure by "nothing". Causes found:

1. **Auth-gated blind candidate starved the mesh.** LM Studio with API
   auth probes "online" (401 = reachable) with an empty catalog. Blind
   candidates always show `in_flight=0`, so least_load picked LM Studio
   first for *every* request → 401 → retry. Fixed: 401/403 backends
   with no catalog are skipped in `backends_for_model` (doctor already
   flags the missing key).
2. **Retries collapsed to local-first.** Attempt ≥ 2 always used
   `failover` ordering, so one flaky backend funneled every retry to
   the local machine regardless of load. Fixed: load-aware strategies
   (least_load, latency_weighted, round_robin, local_spillover) keep
   their strategy on retries; `exclude_ids` already guarantees
   progress.
3. **Phase-5 probe fix backfired.** The 1-token diagnose used to hit an
   embedding model (400, cheap); picking a chat-capable model made
   every routine 10s scan ask the provider to LOAD a chat model —
   memory-pressure evictions on both hosts. Fixed:
   `scan_local_providers(diagnose=False)` is the default; only explicit
   `netllm discover` opts into the inference test.
4. **Stale local rows survived provider removal.** Dropping a provider
   from `discovery.providers` left its pool row routable until restart.
   Fixed: `prune_local_provider_rows` — the scan is authoritative for
   discovery providers (cloud injects and overrides untouched).
5. **Config conflicts across the mesh** are now self-healing:
   `routing.follow_gateway = true` (default) makes peer-role agents
   adopt the gateway's advertised `default_strategy` from heartbeats at
   runtime. Explicit opt-out for intentionally-different peers.
6. **macOS Settings crash (SIGTRAP)** — `routingPolicyEditor` /
   `backendOverrideEditor` bound rows via `$array[index]`; after a
   reload/remove shrank the array, SwiftUI re-evaluated stale ForEach
   children and the subscript trapped (crash report:
   `Array._checkSubscript` via `Binding.subscript.getter`). Fixed with
   bounds-safe Binding accessors; the stray `.id(array[index]…)` in the
   overrides ForEach removed.
7. **Settings shows the resolved LAN address** ("LAN address" row from
   live status) next to the raw bind address, so the advertised IP is
   visible for this machine; the Swarm tab already lists peers with
   their LAN URLs.
8. **Orphaned-agent replace hardening.** `stop_netllm_on_port` waited
   only for the *port* to free — a SIGTERM'd uvicorn releases its
   listener immediately but keeps running until in-flight LLM requests
   drain, still holding its mDNS registration and gossip loop. The
   replacement then hit an mDNS name collision and started with LAN
   advertising permanently disabled (observed after the phase-6 app
   update: "peers detected for a moment, then gone"). Now the stop
   helper waits for process exit and escalates to SIGKILL after the
   grace window.
9. **mDNS advertiser self-heals.** A startup advertise failure no
   longer disables LAN advertising for the agent's lifetime — the
   rediscovery loop retries every `rediscover_interval_s` until the
   collision clears.
10. **Version bumped to 0.4.2.0** across the workspace so the new
    heartbeat version-drift warning can distinguish phase-5/6 builds
    from 0.4.1.0 installs (identical version strings on different code
    defeated it).
11. **Agent subprocess App Nap fix.** The menubar app spawned the serve
    child without a QoS class; macOS App Nap'd it (observed: frozen at
    interpreter startup, ~0 CPU, no sockets, until SIGCONT) while a
    predecessor drained — the health check then failed and installs
    looked broken. `Process.qualityOfService = .userInitiated` keeps
    the network-serving child schedulable.

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
