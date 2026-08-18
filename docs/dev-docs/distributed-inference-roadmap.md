# Distributed inference roadmap

**Status: Planned** — documented only, no code yet.

Companion artifact (same content, presentation form):
[Distributed Inference Capability Trace & Pathway](https://claude.ai/code/artifact/913bf194-440f-43fe-b335-d8495ce08a5a).

As-built truth lives in [`../architecture/`](../architecture/README.md); this doc holds intent.
Component boundaries: [02-component-architecture.md](../architecture/02-component-architecture.md).

---

## Problem

oMLX shipped multi-Mac distributed serving in
[PR #2423](https://github.com/jundot/omlx/pull/2423) (merged 2026-08-12, +53,125/−123
across 133 files), followed by #2591 (heterogeneous Metal + CUDA pool), #2620 (SSD
boundary snapshot prompt cache) and #2697 / #2758 (preflight parity gating). netllm is
the router in front of oMLX on macOS, and the obvious question is which of those
capabilities netllm should expose, orchestrate, or reimplement — and which of them
survive contact with Linux, Windows, and more than one or two release cycles.

netllm today is a **request-level** router. Every abstraction assumes one request maps to
one whole model on one backend. There is no cross-node execution, no prefix-cache concept,
no RTT or bandwidth probing, and no hardware advertisement of any kind.

## Findings that shape the design

Four conclusions were put through adversarial review against primary sources. Three of the
four first-pass conclusions were wrong; the corrected versions are what this plan rests on.

### 1. Tensor parallelism is latency-bound, and the constraint is transport, not OS

TP needs roughly two blocking all-reduce collectives per layer per token. For a 27B-class
model (hidden 5120, 64 layers) that is 128 serialised round trips per token carrying only
about 10 KB each. Bandwidth is not the binding constraint; per-collective latency is.

| Transport | Per collective | ms/token | Ceiling, zero compute |
|-----------|---------------:|---------:|----------------------:|
| RoCEv2, ConnectX-5, 25GbE | ~15 µs | 1.9 | ~520 tok/s |
| JACCL, Thunderbolt 5 RDMA | 50 µs | 6.4 | ~156 tok/s |
| TCP on ConnectX-6 | ~55 µs | 7.0 | ~142 tok/s |
| Kernel TCP, 10GbE | ~200 µs | 25.6 | ~39 tok/s |
| 1GbE / 2.5GbE / Wi-Fi | 500 µs+ | 64+ | <16 tok/s |

oMLX's own `link_bandwidth.py` measures the same Thunderbolt cable at **28.6 tok/s under
RDMA and 6.6 tok/s under the TCP ring — 4.3× from the transport alone, at identical
bandwidth**. Its `fast` predicate reads `source == "measured" or kind in
_COLLECTIVE_CAPABLE_KINDS`, i.e. a measurement overrides the link label, and the module
states outright that kind checks are "a poor proxy". Its nominal formula (0.35 efficiency,
1.0 GB/s floor) admits any link at or above 22.9 Gb/s. oMLX's heterogeneous design puts
Thunderbolt as the *slow outer ring* and Linux + ConnectX-7 Ethernet as the fast inner
fabric.

**Consequence for netllm:** gate distributed features on a **measured probe**, never on a
platform or cable-type check. A platform gate would wrongly exclude the Linux + RoCE
configuration that is the industry's reference for cross-node TP.

**Separately:** TP synchronises 128× per token, so the slowest peer gates every collective.
The netllm swarm is heterogeneous by design. Pipeline parallelism tolerates that through
non-uniform layer sharding; tensor parallelism does not. That incompatibility holds
regardless of transport, and it — not physics — is why netllm should not pursue TP.

### 2. Cache-aware routing from oMLX telemetry does not work

The first-pass plan was to feed oMLX's cache telemetry into `RouterPool.select_backend`.
It fails on six independent grounds:

- `status_payload_enriched()` calls `probe_omlx_admin_for_backends`, which returns only
  `loaded_models` and `primary_loaded_model` — no cache fields at all.
- Three of the five candidate fields come from `_normalize_omlx_stats_payload`, which has
  zero production callers, and `model_memory_used` is hardcoded `0` on the live path.
- The two surviving fields are node-wide, all-model, cumulative-lifetime scalars. They
  cannot discriminate between candidates.
- They are never gossiped, and the probe structurally cannot see a peer: it skips
  `provider != "omlx"` while peers materialise as `provider="custom"`.
- The data only exists while a telemetry request is in flight, so headless operation never
  populates it.
- `RouterPool.select_backend` is synchronous and lives in `netllm-core`, upstream of both
  discovery and telemetry.

`latency_weighted` works where this does not, because its EMA is maintained on the hot path
by `mark_success()` — free, no polling, no backend cooperation. **Durable routing signals
are hot-path-derived.** That is the design rule Phase 2 follows.

### 3. Cross-node prompt-cache pooling is not an oMLX feature to port

oMLX's boundary snapshot cache is strictly per-rank local disk: identical keys across ranks,
but "the bytes under a key are this rank's shard alone". Only a one-hot integer agreement
vote crosses the wire. Zero KV bytes move between machines. Anything netllm builds here is
additive rather than duplicative.

### 4. oMLX cluster state cannot be routed on — the signal does not exist

Not a maturity judgement. Per oMLX's own open issues, a non-coordinator node answers `/v1`
silently from its own model indistinguishably from a distributed answer
([#2681](https://github.com/jundot/omlx/issues/2681)); the coordinator returns HTTP 200 with
a 1-byte body when a rank is alive but not listening
([#2708](https://github.com/jundot/omlx/issues/2708)); `/health` returns 200 while
completions hang ([#1884](https://github.com/jundot/omlx/issues/1884)); and coordinator loss
means no failover, with each rank silently reverting to standalone
([#2682](https://github.com/jundot/omlx/issues/2682)). A caller cannot discover the
coordinator's address without reading the dashboard.

Every `/admin/api/cluster/*` endpoint sits behind the `omlx_admin_session` cookie. The only
bearer-reachable cluster signal is `GET /v1/models/status` → `distributed: bool` +
`source_type: "cluster"`, and that field first appeared in v0.6.0.dev1 on 2026-08-13.

Note also that oMLX enforces `CLUSTER_PROTOCOL_VERSION` by **exact string equality** with no
negotiation and no feature bits — the opposite of netllm's additive skew promise in
[compatibility-policy.md](../compatibility-policy.md) and [mesh-upgrade.md](../mesh-upgrade.md).
Any oMLX version skew across peers is a total cluster outage, not a degradation. netllm must
treat "cluster available" as a fact that can vanish on any peer's upgrade.

## Decision matrix

Scored 1–5 per criterion. Durability — survives two-plus releases without a rewrite — is
weighted highest, per the brief.

| Capability | Durable 30% | Cross-plat 20% | Testable 15% | Fits seams 20% | Value 15% | Score |
|------------|------------:|---------------:|-------------:|---------------:|----------:|------:|
| Measured link probing + capability advertisement | 5 | 5 | 5 | 4 | 4 | **4.65** |
| Hot-path prefix affinity routing | 5 | 5 | 5 | 3 | 4 | **4.45** |
| Cross-node prompt-cache migration | 4 | 5 | 5 | 3 | 5 | **4.30** |
| llama.cpp RPC cluster orchestration | 4 | 5 | 5 | 3 | 4 | **4.15** |
| oMLX cluster capability tag (display only) | 2 | 1 | 5 | 5 | 2 | 2.85 |
| Native TP/PP data plane in netllm | 1 | 2 | 2 | 1 | 3 | 1.65 |
| vLLM KV connector implementation | 1 | 1 | 3 | 1 | 2 | 1.45 |

The top four share a property: each depends either on nothing external, or on an interface
maintained by a core maintainer at steady cadence for 20+ months. The bottom three each
depend on something six days old, churning monthly, or that we would maintain forever.

## Phases

### Phase 0 — repair the broken oMLX admin coupling

`probe_omlx_telemetry` in `packages/netllm-discovery/src/netllm_discovery/local.py` calls
oMLX's `/admin/api/stats` and `/admin/api/activity` with a bare client carrying **no
authentication of any kind**, wrapped in `except Exception: pass`. oMLX protects those
endpoints with an admin session cookie, so against any authenticated oMLX these 401 silently
and permanently. netllm already has a dead admin integration.

Smallest correct fix is likely to make the failure visible rather than swallow it, and
surface an "auth required" state so the blank telemetry panel explains itself. Deleting the
probe is also defensible — see the dead-code notes in finding 2 above.

Proposed finding ID **F-98** (next free in the audit tail); register it in
`docs/architecture/07-findings-register.md` when this plan is accepted.

**Exit gate:** a test covering the 401 path; `./scripts/ci.sh` green; telemetry contract
tests still pass.

### Phase 1 — measured peer link probing

Nothing else here is safe without it, and there is currently no RTT or bandwidth probing
anywhere in the tree. This is the "pre-test for latency and viability" the roadmap turns on,
and finding 1 says it must be a measurement rather than a platform check.

- Measure **RTT and jitter first** — the dominant predictor — then bulk throughput, which
  only predicts first-load time.
- New probe module in `netllm-discovery` beside `lan.probe_agent_port` in
  `packages/netllm-discovery/src/netllm_discovery/lan.py`.
- Land results on `PeerRecord` in
  `packages/netllm-discovery/src/netllm_discovery/swarm.py`, then on `Backend` in
  `packages/netllm-core/src/netllm_core/models.py`, so the existing `latency_weighted`
  strategy in `packages/netllm-core/src/netllm_core/pool.py` consumes them.
- New wire fields are additive, read with explicit `.get()` on both sides, with a
  `normalize_*` coercer following the established precedent.
- Borrow oMLX's discipline: a partial or non-finite result discards **all** measurements
  rather than planning on half-data. Its `strategy_benchmarks.py` refuses to rank strategies
  unless every candidate has been measured — "a single measurement is not a comparison".

**Exit gate:** probe results visible in `./netllm status` and the peers dashboard page;
mixed-version mesh test proving an older peer without the fields still routes; forward-compat
config test green.

### Phase 2 — hot-path prefix affinity routing

Replaces the refuted telemetry-scraping approach. netllm hashes the prompt prefix of the
request it is about to forward and remembers which backend served it. No admin API, no
cookie, no provider-specific branch; identical behaviour for oMLX, Ollama, LM Studio, vLLM
and llama.cpp, and it survives oMLX cluster mode, where cache telemetry reports ~0%
permanently.

Two hazards:

- Affinity fights load balancing. `max_in_flight_per_backend` defaults to `0` (off), so on a
  default install nothing opposes a stampede onto the warm node. Pair affinity with an
  actual cap.
- Do not mix a time-varying score into `shard_index`; it destroys the rendezvous-hashing
  stability contract that function's docstring promises.

Note `routing.model_groups` is documented but unbuilt in
[routing-hardening-plan.md](../routing-hardening-plan.md); `ModelGroup` in
`packages/netllm-core/src/netllm_core/model_resolution.py` already exists and already
accepts injected groups.

**Exit gate:** affinity demonstrably raises measured cache hit rate on a two-node mesh
without regressing p99 under load; stampede test with the cap enabled.

### Phase 3 — cross-node prompt-cache migration

The differentiated bet, and the highest payoff per unit of effort. llama.cpp exposes slot
save and restore with a server-local slot path, but those endpoints only name a file *within
that path* — there is no HTTP upload or download of the blob. Moving state between hosts
needs filesystem access at both ends, which is exactly what an agent on every host provides.
Nobody is doing this for local or LAN setups.

- Economics: roughly 8.2 KB/token in the documented example, so a 1,745-token state is
  ~14.3 MB ≈ 0.12 s on 1GbE, against 1–3 s to re-prefill.
- Portable: no GPU-backend coupling — a state saved on Metal restores on CUDA.
- Compatibility key: model architecture, KV cache quantisation types, flash-attention
  setting, state-format version, and target context ≥ saved cell count. All readable from
  llama.cpp's `/props`.
- Hazard: the format carries **no model hash**, so a wrong model may load garbage rather
  than erroring. netllm must verify model identity itself.

**Exit gate:** a prefix cached on host A demonstrably restored on host B across two
different GPU backends, with the compatibility key correctly refusing a mismatched pair.

### Phase 4 — llama.cpp RPC cluster orchestration

The only mechanism that works across macOS, Linux **and** native Windows with mixed
CUDA / Metal / Vulkan / CPU backends in one cluster. Official release builds ship with RPC
enabled on every platform, so it bootstraps from a downloaded archive.

- Position it as "run a model too big for any one machine". It is **memory aggregation, not
  throughput aggregation** — every async backend op is `NULL`, so only one node computes at
  a time, and it is slower than a single box that fits the model. Never advertise it as a
  speedup.
- Gate cluster formation on matching build info from `/props`. The protocol is pinned to the
  ggml op enum by a `static_assert`, so adding ops bumps the protocol and mismatched builds
  are rejected outright. Cluster cap is 16 servers.
- **Security is netllm's problem.** The RPC protocol has no authentication and no
  encryption, upstream documents it as "fragile and insecure" and says never to run it on an
  open network, and a critical unauthenticated RCE landed in March 2026 (GHSA-j8rj-fmpv-wcxw)
  alongside three earlier memory-safety advisories. Bind rpc-servers to loopback and tunnel
  over the authenticated mesh, or gate on `swarm.cluster_token`. Never bind to `0.0.0.0`.

Note llama.cpp is **not** currently a registered local provider — adding it is Axis B work
under [extending/](../extending/README.md), with its own registry entry and companions.

**Exit gate:** a model larger than any single node's memory served across three mixed-OS
nodes; build-info mismatch refused with an actionable message; no rpc-server reachable off
loopback in the shipped configuration.

### Phase 5 — oMLX cluster capability tag, display only

Poll `GET /v1/models/status` with the bearer key netllm already holds; treat
`distributed: true` or `source_type == "cluster"` as a capability tag. Fail closed on a
missing field, 401, 404, 5xx or malformed body.

**Never route on it, never gate admission on it** — see finding 4. Rule out the Bonjour
`_omlx._tcp` TXT records entirely: they carry only hostname, version and ssh_port, with no
cluster data. Revisit routing when oMLX #2681 and #2708 close.

**Exit gate:** tag renders in all three surfaces; a peer running oMLX without the field, or
with auth failing, shows no tag and logs nothing alarming.

## Out of scope

- **Native TP/PP data plane in netllm.** No seam exists. `run_with_failover` in
  `packages/netllm-agent/src/netllm_agent/service/engine.py` is strictly serial,
  `MAX_FORWARD_HOPS = 2` caps any A→B→C chain, and `scripts/check-engine-erosion.py` forbids
  new branches in the failover loop. By the extension program's own logic this needs a new
  axis guide and conformance kit written *before* the code.
- **Any vLLM KV connector implementation.** An in-process plugin into engine internals —
  tensors, attention metadata, block pools — not something a Python router drives over HTTP.
  Documented as experimental and subject to change; still named v1 while churning monthly,
  with connectors appearing and vanishing between releases.
- **exo, petals, distributed-llama, DeepSpeed-MII.** Abandonment risks regardless of star
  count: 10 commits in 90 days, dead since 2024, 1 commit in 90 days, and no push in 14
  months respectively.

## Platform and surface coverage

| Capability | macOS | Linux | Windows |
|------------|-------|-------|---------|
| Measured link probing | yes | yes | yes |
| Prefix affinity routing | yes | yes | yes |
| Prompt-cache migration | yes | yes | yes |
| RPC cluster | yes | yes | yes |
| oMLX cluster tag | yes | no | no |
| Cross-node tensor parallelism | TB5 mesh only | RDMA/RoCE only | none — NCCL is Linux-only |

Every capability lands three times: SwiftUI in the macOS menubar app, the web dashboard page
modules, and the CLI. The extension program generates the manifest of what must exist, never
the UI itself, and `ControlDescriptor` makes omission a red test rather than a smaller job.
Windows has no native UI and no lifecycle module, so it is CLI plus web dashboard only.
Current platform truth: [platform-matrix.md](../platform-matrix.md).

## PR slicing

One PR per phase, in order. Phase 0 is independent and can land immediately. Phase 1 gates
everything after it, because Phases 3 and 4 both need a viability probe before forming a
topology. Phase 5 is independent of 1–4 and can land at any point.

Phase 4 additionally needs an Axis B registry entry for llama.cpp, which is its own reviewable
slice and should land ahead of the orchestration work.

## Open questions

- No first-party RoCE latency measurement on ConnectX-5, and no current NIC pricing to
  confirm "commodity". Both bear on whether the Linux RDMA path is worth supporting at all.
- Adopting the Kubernetes SIG Gateway API Inference Extension metric names for netllm's own
  `/metrics` costs nothing and improves tooling legibility, but the exact required names were
  not verified. See also [telemetry-api.md](../telemetry-api.md).
- llama.cpp exposes neither KV-cache usage nor prefix-cache hit rate in 2026 — the older
  KV-usage metric is gone. A cache-aware router must derive both. Deferred-request count and
  the slot endpoint's saturation response are real admission signals available today.
- `packages/netllm-core/src/netllm_core/pool.py` is not in the mirror ledger scan list in
  `tests/conformance/ledgers/mirrors.toml`, so a provider literal in the router would *escape*
  the gate rather than trip it. Worth closing independently of this roadmap.

---

Updated: 2026-08-19 (initial plan)
