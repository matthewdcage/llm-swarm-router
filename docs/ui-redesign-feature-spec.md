# UI redesign — backend feature spec

Status: **proposed**. Written 2026-08-10 against `feat/ui-redesign-swarm`
(dashboard rebuilt to 11 page modules under
`packages/netllm-agent/src/netllm_agent/static/pages/`). Nothing here is
implemented.

## What this document is

During the redesign, each of the 11 page builds recorded every element the
mockup draws that the agent cannot currently feed. That produced 94 gap lines.
This document clusters them into 12 buildable features (`UI-1` … `UI-12`),
gives each a contract, a surface obligation, a test regime, a cost and a risk,
and ranks them into three tranches. §5 names the gaps that should be closed by
**changing the design** rather than building backend. §6 is the full 94-row
index, so nothing is lost.

Feature ids are `UI-nn` deliberately: `F-nn` is taken by
[architecture/07-findings-register.md](architecture/07-findings-register.md).

**Every claim about current behaviour below cites a file.** Where the current
behaviour was not established by reading code, it says so.

---

## 0. Prerequisite — `UI-0`: re-anchor Axis D at the new page modules

Not a gap-set item; a blocker for most of what follows.

`netllm_core.control_plane.CONTROLS` names a `dashboard_renderer` per control
(`control_plane.py:92-238`: `renderStatusTab`, `renderServingTab`,
`renderBackendsTab`, `renderAgentTab`, `renderRoutingTab`, `renderUiTab`,
`renderCloudTab`, `renderSourcesTab`, `renderLogsTab`, `renderToolsTab`, …).
`tests/conformance/kit_config_surfaces.py:900-915` asserts each of those is a
value in `const TAB_RENDERERS` in `dashboard.js` **and** that
`function <renderer>(` is defined there.

On this branch none of that exists. `dashboard.js` now has `PAGE_RENDERERS`
plus a `registerPage(key, fn)` call at the bottom of each `static/pages/*.js`
module; `renderAgentTab`, `renderDiscoveryTab`, `renderSwarmTab`,
`renderRoutingTab`, `renderSourcesTab` and `renderUiTab` are gone entirely, and
`renderStatusTab` / `renderServingTab` / `renderBackendsTab` / `renderModelsTab`
survive only inside comments (`pages/overview.js:5-6`, `pages/backends.js:3`,
`pages/models.js:3`). The `EditingSurface` region anchors
(`kit_config_surfaces.py:440-500`: `Slice(DASHBOARD_JS, "function
renderAgentTab", "function renderDiscoveryTab")`, …) point at the same removed
symbols.

Consequence: **the control-parity gate is currently either failing or
vacuous**, and the repo rule this spec is written under — a new config field
needs a control on both the dashboard and the macOS app, or a dated row in
`tests/conformance/ledgers/control-parity.toml` — cannot be enforced until the
anchors move. `UI-0` is: update `CONTROLS.dashboard_renderer` to the new
`renderXPage` identifiers, point `Region.slices` at `static/pages/<page>.js`,
and teach the kit that `registerPage("<key>", …)` is the new registry
assertion. Cost **S**. Do it first; every config field in `UI-7`, `UI-8` and
`UI-12` lands on top of it.

---

## 1. The two facts that explain half the gap set

**(a) Every request counter in the agent is cumulative since process start,
undimensioned, and carries no start timestamp.**
`RouterPool.routed_counts` is `{backend_id: int}` incremented in
`mark_success` (`netllm_core/pool.py:283`); `_source_counts` is
`{source_id: int}` incremented in `_attribute_source`
(`netllm_agent/service/policy.py:192`); `_scenario_counts` is
`{(source_id, scenario): int}` (`policy.py:232`). All three are emitted raw by
`status_payload` (`netllm_agent/service/status.py:36-41`) and the first by
`_router_block` (`netllm_agent/telemetry.py:246`). Nothing is keyed by model,
by API surface, by policy, or by pool; nothing is windowed; nothing says when
counting started. The only persisted counters (`stats.json`,
`telemetry.py:97-114`) are agent-wide totals with no per-backend split.

That single fact is why the design's entire vocabulary — "last 5 min",
"412 requests today", "by user-agent, last 24h", "matched 214× today", "share
of traffic", "no request has left your network in 30 days" — has nothing to
read. It is `UI-1`.

**(b) The router does not measure time-to-first-token, and the prefill/
generation split it reports is fabricated.**
`AttemptRecorder.success` calls
`telemetry.record_usage(prefill_duration=latency_s * 0.3, generation_duration=
latency_s * 0.7)` (`netllm_agent/service/accounting.py:91-96`). `avg_prefill_tps`
and `avg_generation_tps` (`telemetry.py:36-44`) are therefore
`prompt_tokens / (0.3 × total_latency)` and
`completion_tokens / (0.7 × total_latency)` — two numbers derived from a
hardcoded constant, shipped to the dashboard and the macOS menubar as
measurements. This is worse than the missing TTFT the Overview gap asks for:
it is a **wrong number already on screen**. It is `UI-2`.

A third, smaller one worth naming here: `history.router_rps`
(`telemetry.py:204-209`) is only appended to from inside `record_usage`, so an
idle router never samples. The sparkline holds its last values instead of
decaying to zero, and the ring buffer's 60 entries span an unknown wall-clock
duration. Fixed as part of `UI-1`.

---

## 2. Features

Each feature states: gaps closed · current behaviour · contract · surfaces ·
tests · cost · risk.

### UI-1 — Windowed, dimensioned request ledger

**Closes** (13): overview *5-minute traffic window*, *Per-pool traffic share*;
peers *live per-peer share*; models *windowed per-pool request + latency
metrics* (counter half); routing *per-policy match counts*; integrations
*rolling 24h / today window*, *API surface per client*, *model most used per
client*, *per-client live indicator*, *cumulative-vs-window scenario labels*;
cloud *30-day "nothing left your network" window*; backends *(share column
inputs)*; overview *Live tok/s* (request-rate half).

**Current** — see §1(a).

**Contract.** One new object in `netllm_agent`, `RequestLedger`, owning
fixed-width second buckets, replacing nothing (the existing cumulative dicts
stay; they are a different question and clients read them today). Written from
the two places that already count — `AttemptRecorder.success`
(`accounting.py:69`) and `PolicyMixin._attribute_source` /
`_classify_and_record_scenario` (`policy.py:182,210`) — plus one new call from
`resolve_routing`'s caller for the policy dimension.

Emitted on `GET /netllm/v1/telemetry` under `router.windows`:

```json
"router": {
  "windows": {
    "counters_since": 1786700000.0,
    "spans_s": [60, 300, 86400],
    "by_backend": { "<backend id>": { "60": 4, "300": 38, "86400": 4102 } },
    "by_model":   { "<model id>":   { "60": 4, "300": 38, "86400": 4102 } },
    "by_policy":  { "<policy key>": { "60": 0, "300": 12, "86400": 214 } },
    "by_source":  {
      "<source id>": {
        "requests":   { "60": 1, "300": 9, "86400": 412 },
        "surfaces":   { "openai": 400, "anthropic": 12 },
        "top_models": [ { "model": "gemma4:27b", "count": 300 } ],
        "last_seen_at": 1786786400.0
      }
    },
    "truncated": { "by_model": 3, "by_source": 0 }
  }
}
```

- `spans_s` is server-declared, not client-assumed. Clients read the span keys
  present; they never sum buckets themselves (same rule as `total_tokens`,
  `docs/telemetry-api.md` "Normative key set").
- `<policy key>` is `"<index>:<name>"` — `RoutingPolicy` has no id
  (`netllm_core/models.py:166-179`) and `policies` is an ordered list, so index
  alone breaks on reorder and name alone breaks on the empty default name.
  A stable `RoutingPolicy.id: str = ""` (server-minted on first save) is the
  better answer and is specified under `UI-9`; until then the composite key is
  what is honest, and the payload must be readable without it (`"never
  matched"` is `{}`, not a missing key).
- `by_source[].last_seen_at` is what the design's per-client live dot needs; a
  PATH check (`harnesses[].detected`) is explicitly not traffic.
- `truncated` counts dimension keys dropped by the cardinality cap (below).
  A UI that shows a top-N list must be able to say the list is partial.

Also on `GET /netllm/v1/status`, additively:
`counters_since: float` (epoch) and
`scenario_requests_by_source: {source_id: {scenario: count}}`. The existing
flat `scenario_requests` key (`"<source>:<scenario>"`,
`service/status.py:38-41`) is kept and gets a row in
`netllm_core.deprecations.DEPRECATIONS` with a `remove_in` — it mis-parses for
any source id containing a colon, which is the integrations gap's complaint,
and the deprecation clock is how this repo retires wire keys
(`packages/netllm-core/src/netllm_core/deprecations.py`).

No new config. No new endpoint.

**Surfaces.** web: every windowed figure on Overview / Peers / Models /
Routing / Integrations, each labelled with the span it came from. swift:
`ServingStatsMenuBuilder` / `TelemetryPoller` read `router.windows.by_backend`
for the menubar's live figure; the rest is optional and gets a `[[control]]`
ledger row if skipped (the `serving` unit already has one, expiring
`phase-8`). cli: `netllm status` prints the 5-minute per-backend split
alongside the cumulative one.

**Tests.**
- unit `tests/test_telemetry.py`: a ledger fed synthetic timestamps returns the
  right counts per span; buckets age out exactly at the span boundary; a
  request recorded at T is invisible at T+span+1; idle time produces zeros, not
  stale values.
- unit: cardinality cap — 10 000 distinct model names produce a bounded dict
  and a non-zero `truncated.by_model`.
- contract `tests/contract/test_telemetry_contract.py`: the frozen key set gains
  `windows` under `ROUTER_KEYS` plus the nested roster, and
  `docs/telemetry-api.md` gains matching rows. That test asserts both
  directions, so a key emitted without a doc row fails CI and vice versa.
- contract `tests/contract/test_metrics_parity.py`: `netllm_policy_requests_total`
  (`{policy}`) and a `surface` label on `netllm_source_requests_total`
  (`netllm_agent/metrics.py:32-41`) match the payload dimensions.
- e2e `tests/e2e/test_dashboard_e2e.py`: drive N chat completions through the
  stub backend, then assert the Peers page share column and the Integrations
  request column show non-zero, and that the column header names a window
  rather than "since this agent started".
- perf guard: a micro-benchmark asserting `RequestLedger.record()` allocates no
  dict/list per call and stays under a fixed ns budget. Not a wall-clock
  assertion in CI (flaky); an allocation-count assertion via `tracemalloc`.

**Cost: M.** The ledger is small; the work is in the six call sites, the
per-dimension cardinality policy, the doc rows, and the client changes.

**Risk — hot path.** This is the one feature that writes on every completion.
Constraints, non-negotiable:
1. Fixed-size preallocated integer ring per (dimension, key). Recording is
   `bucket[(now_s) % width] += 1` after a stale-bucket zero — no timestamp
   list, no append, no prune scan, no allocation on the steady path.
2. No lock. The existing counters are lock-free plain dicts on the event loop
   (`policy.py:192`); adding an `asyncio.Lock` around per-request accounting
   would serialise completions.
3. **Cardinality is attacker-controlled.** `by_model` is keyed on the client's
   requested model string and `by_source` partly on User-Agent; a client
   sending random model names grows this unboundedly. Hard cap per dimension
   (proposed 256 keys), evict lowest-count, count evictions in `truncated`.
   `tests/e2e/conftest.py:92` already routes a hostile model id through the
   stack — reuse it.
4. Cross-tab dimensions multiply. `by_source[].top_models` is the only 2-D
   view specified, and it is a bounded top-N per source, not a full matrix.
   Do not add `(source × model × surface)`.
5. `history.router_rps` must move to a timer sample, not a request-triggered
   one, or the idle-freeze in §1 persists. That timer must not wake a sleeping
   laptop's event loop more often than the existing heartbeat.

---

### UI-2 — Real latency: TTFT, per-backend percentiles, live tok/s

**Closes** (5): overview *p50 TTFT*, *Live tok/s* (rate half); backends
*per-backend p50 latency*; models *windowed per-pool ... p50* (latency half);
routing *TTFT stat for a test request* (with `UI-9`).

**Current** — see §1(b). Additionally `BackendHealth.latency_p50_ms`
(`models.py:585`) is written only by a full discovery scan and
`Backend.latency_ema_ms` (`models.py:611`) only after traffic
(`pool.py:285-288`), so a merely health-probed backend renders `—`.

**Contract.**

1. **Measure TTFT.** The streaming wrappers already own first-chunk position;
   record `time.perf_counter()` at the first non-empty SSE payload and pass it
   to `AttemptRecorder.success` as `ttft_s`. Non-streaming responses have no
   TTFT and must be **excluded from the percentile**, not folded in as total
   latency — a mixed population makes p50 meaningless. Emit
   `ttft_samples` so a client can see the population size.
2. **Delete the fabrication.** `accounting.py:94-95` stops passing
   `latency_s * 0.3` / `latency_s * 0.7`. `prefill_duration` becomes the
   measured TTFT; `generation_duration` becomes `latency_s - ttft_s`. For a
   non-streaming request both are unknown: pass `0.0` and do not increment the
   duration accumulators, so `avg_prefill_tps` stops being a function of a
   constant. This changes `router.session.avg_prefill_tps` for existing users —
   it is a correction, and belongs in release notes as one.
3. **Percentiles are histograms, not reservoirs.** Fixed log-spaced buckets
   (reuse `REQUEST_LATENCY`'s shape, `netllm_agent/metrics.py:14-19`),
   interpolated at read time. O(1) record, zero allocation, no sort.
4. New telemetry keys:

```json
"router": {
  "latency": { "ttft_p50_ms": 188.0, "ttft_p95_ms": 402.0,
               "ttft_samples": 91, "window_s": 300 },
  "live":    { "prefill_tps": 0.0, "generation_tps": 1284.0,
               "requests_per_s": 0.4, "window_s": 10 },
  "backends": [ { "id": "…", "p50_ms": 142.0, "p95_ms": 310.0,
                  "samples": 41, "window_s": 300 } ]
},
"history": { "router_tps": [] }
```
   `router.backends[]` is an existing array (`telemetry.py:250-260`); the three
   keys are additive to each row.
5. `Backend.latency_p50_ms` / `latency_p95_ms` (runtime model, alongside
   `latency_ema_ms`) so `GET /netllm/v1/status` `backends[]` carries them too —
   the Backends page reads status, not telemetry.

**Surfaces.** web: Overview throughput panel, Backends p50 column, Models pool
header. swift: menubar live figure already reads `omlx.live`; point it at
`router.live` when no oMLX backend exists. cli: `netllm status --verbose` gains
a p50 column.

**Tests.**
- unit `tests/test_telemetry.py`: histogram percentile against a known
  distribution, including the single-sample and zero-sample cases (must emit
  `null`, never `0.0`, for "no data" — the whole point is not lying).
- unit: a non-streaming request contributes to `requests` and `latency_ema_ms`
  but not to `ttft_samples`.
- unit: the regression guard — assert `accounting.py` no longer multiplies
  latency by a constant, by asserting `avg_prefill_tps` is `0.0` after a
  non-streaming request that reported prompt tokens.
- contract `test_telemetry_contract.py` + `docs/telemetry-api.md` rows for
  `latency`, `live`, `history.router_tps`, and the three new `backends[]` keys.
- e2e: stub backend with an artificial delay before first chunk; assert the
  Overview TTFT stat renders a number and the Backends p50 cell is not `—`.

**Cost: M.** Small code, wide blast radius: five proxy paths reach
`AttemptRecorder`, and the corrected `avg_prefill_tps` is a visible behaviour
change on two existing surfaces.

**Risk.** (i) `perf_counter()` twice per request is negligible; the risk is
threading the first-chunk timestamp through the streaming wrappers without
duplicating it per proxy path — `AttemptRecorder` exists precisely because that
duplication caused F-24, so the timestamp must arrive through the recorder, not
through five call sites. (ii) The corrected prefill figure will look like a
regression to anyone who trusted the old one; needs a release note.
(iii) Do not add a Prometheus histogram per backend id — backend ids are
bounded but cloud/peer churn makes the label set grow; the existing
`REQUEST_LATENCY{backend=base_url}` already covers it.

---

### UI-3 — Wall-clock honesty: epoch timestamps on everything the UI ages

**Closes** (8): backends *last-probe wall clock*; network *last provider scan
time*, *cluster token provenance* (date half); peers *last full scan timestamp*,
*heartbeat age for scan-only rows*; doctor *doctor run timestamp*; preferences
*update last-checked timestamp*; cloud *masked key ... stored-at date* (date
half; the hint half is `UI-7`).

**Current.** `BackendHealth.last_check` is `time.monotonic()`
(`pool.py:340,357`) — process-relative, so a browser cannot age it.
`peers_scan_payload` and `POST /netllm/v1/admin/discover`
(`routes/admin.py:136-154`) return no timestamp. `doctor_payload`
(`admin.py:50`) returns no timestamp. `build_update_check_payload` returns none
(the 900 s `_RELEASE_CACHE` is internal to `netllm_core/update.py`).
`swarm.cluster_token_set` is a bool (`admin.py:381`).

**Contract.** One rule: *any value the UI renders as an age carries an epoch
seconds float from the server; the client never infers age from its own fetch
time.* Concretely, additive fields:

| Payload | New key | Type |
|---|---|---|
| `BackendHealth` | `last_check_epoch_s` | `float` (0.0 = never probed) |
| `GET /netllm/v1/status` | `discovery.last_scan_at` | `float \| null` |
| `GET /netllm/v1/status` | `counters_since` | `float` (see `UI-1`) |
| `POST /netllm/v1/admin/discover` | `last_scan_at` | `float` |
| `POST /netllm/v1/admin/peers-scan` | `last_scan_at`, per row `probed_at` | `float` |
| `GET /netllm/v1/doctor` | `checked_at` | `float` |
| `GET /netllm/v1/update/check` | `checked_at` | `float` |
| `GET /netllm/v1/config` `swarm` | `cluster_token_set_at` | `float \| null` |

`last_check` keeps its monotonic meaning — `pool.py` does freshness arithmetic
with it (`_freshness_s`, `pool.py:306`; callers at `pool.py:302,320,372`) and converting it would be a
behaviour change inside the health cache. Add the epoch sibling; do not
convert.

`cluster_token_set_at` needs a writer: `netllm join` / the rotate path stamps
it. A config field (`swarm.cluster_token_set_at: float = 0.0`, read-only) is
the honest home, which means it is a schema field and therefore falls under the
parity rule — it is `read_only`, so both generic renderers drop it by
construction and it is excluded from the parity denominator
(`kit_config_surfaces.py` "derived" disposition). No ledger row needed.

**Surfaces.** web + swift render ages; cli prints absolute times. No new
control.

**Tests.**
- unit per payload: the key is present, is a float, and is within a second of
  `time.time()` after the operation that sets it.
- unit: a never-probed backend reports `last_check_epoch_s == 0.0` and the
  dashboard renders "never", not "56 years ago" — assert the sentinel is `0.0`
  and documented, not `null`, so it matches `last_check`'s existing convention.
- contract `tests/test_contract.py` route set unchanged (no new routes).
- conformance: `swarm.cluster_token_set_at` appears in
  `config_schema_document()` with `read_only: true` (`tests/test_config_schema.py`).
- e2e: Backends page subtitle shows a probe age; Doctor header shows "ran Ns
  ago" and the number changes after clicking re-run.

**Cost: S.** Eight additive fields, no new logic, no hot path.

**Risk.** Low. The only trap is stamping `last_scan_at` in the wrong place —
it must be set by the discovery pass itself, not by the route handler, or a
scan triggered by the rediscover timer leaves it stale and the UI claims the
last scan was whenever someone last clicked the button.

---

### UI-4 — Peer heartbeat enrichment

**Closes** (9): overview *Per-node GPU / VRAM*, *Peer backend provider + model
count on the node card*, *Mesh-total throughput series*; backends *per-node
VRAM / GPU capacity* (remote half); peers *discovery mechanism per peer*,
*also_reachable_at for connected peers*, *per-peer detail drill-in*; network
*peer discovery source*, *cluster token provenance* (authenticated-count half).

**Current.** `PeerRecord` is `{agent_id, listen_url, role, hostname, last_seen,
backends, routing_strategy, version, max_concurrency, draining}`
(`netllm_discovery/swarm.py:20-39`) and `all_peer_urls()` emits only seven of
those (`swarm.py:256-268`). A peer is materialised as **one** `Backend` with
`provider="custom"` (`swarm.py:110-143`), so its real provider mix is invisible.
`telemetry.host` is this machine only (`telemetry.py:292-304`, psutil, no GPU,
no VRAM). Nothing records how a peer was found.

**Contract.**

1. `PeerRecord` gains `discovered_via: Literal["mdns","subnet_scan","static",
   "heartbeat","join"] = "heartbeat"`, `also_reachable_at: list[str] = []`,
   `host: dict | None = None`, `providers: list[dict] = []`,
   `rps_60s: float = 0.0`, `authenticated: bool = False`. All six are added to
   `all_peer_urls()` output, hence to `status.peers[]`.
2. The heartbeat body (`POST /netllm/v1/heartbeat`) gains a `host` block —
   `{cpu_percent, memory_used_gb, memory_total_gb, gpu_percent, vram_used_gb,
   vram_total_gb}`, every field nullable — a `providers` list
   `[{id, provider, model_count}]`, and `rps_60s` from `UI-1`. A peer that does
   not send them is a peer running an older netllm: the fields are absent, the
   UI shows `—`, and that is the correct outcome (`docs/mesh-upgrade.md` skew
   promise).
3. `discovered_via` is set at the point of discovery — mDNS listener, subnet
   scanner, static-peer loader, `netllm join` handler — not inferred later.
4. `GET /netllm/v1/peers/{agent_id}` returning that peer's full record plus its
   backend list, model count, `in_flight`, `max_concurrency` and last probe
   error. Read-gated like `/netllm/v1/status`, not admin-gated.
5. GPU/VRAM has **no cross-platform source**. `psutil` has none. Realistic
   coverage: macOS via `ioreg`/Metal (or nothing), NVIDIA via `nvidia-smi` if
   present, everything else `null`. The contract is "nullable, best effort,
   never blocks the heartbeat"; a probe that shells out must be cached on a
   timer, never run inline.

**Surfaces.** web: Overview node cards, Peers table Discovered/Details columns,
Backends remote table VRAM column, Network known-agents Source column. swift:
peer list detail. cli: `netllm status` peers table gains a source column.
No config field, so no parity obligation.

**Tests.**
- unit `tests/test_swarm.py`: `all_peer_urls()` emits all six new keys; a
  `PeerRecord` built from a heartbeat body missing `host` yields `host: None`
  rather than raising.
- unit `tests/test_mesh_version_skew.py`: an old-shaped heartbeat (no `host`,
  no `providers`) registers the peer with no warning and no crash.
- unit: `discovered_via` is `"mdns"` for an mDNS arrival and `"static"` for a
  `swarm.peers` entry, asserted through the real registration paths, not by
  setting the field.
- contract `tests/contract/routes.json` regenerated via
  `scripts/generate-routes-json.py` for `/netllm/v1/peers/{agent_id}`;
  `tests/contract/route-auth-gates.json` records its gate as read-access.
- e2e: two-agent fixture (`tests/test_e2e_two_agents.py` has the shape) driving
  the Peers page — assert Source reads `mDNS`/`pinned`, not `heartbeat`.

**Cost: M** without GPU/VRAM, **L** with it. The heartbeat and registry changes
are mechanical; per-platform GPU probing is the expensive, low-confidence part.
Recommend shipping `host` with `gpu_percent`/`vram_*` as `null` on every
platform in the first pass and treating GPU as its own follow-up.

**Risk.** (i) Heartbeat payload growth × peers × interval — a `providers` list
per peer per 10 s across a 10-node mesh is fine; a per-backend model-name list
is not. Cap `providers` to `{id, provider, model_count}` and do not send model
names (they are already reachable via the peer's `/v1/models`). (ii) A GPU
probe that shells out on the heartbeat path stalls gossip; must be a cached
background sample. (iii) `authenticated` must reflect the token check actually
performed on that peer's last inbound heartbeat, not `bool(cluster_token)` —
otherwise the Network page's "shared with 3 agents" is a restatement of local
config.

---

### UI-5 — Backend probe enrichment: context, quantisation, residency, probe history

**Closes** (5): models *per-node context window*, *per-node quantisation*,
*warm residency / keep warm* (observation half); backends *probe history
sparkline*; backends *per-node VRAM / GPU capacity* (local half).

**Current.** `BackendHealth` is `{status, http_status, model_count, models[],
detail, latency_p50_ms, last_check}` (`models.py:579-586`). `/v1/models` rows
are `{id, object, owned_by, capability}` (`service/status.py:150-158`).
`telemetry.history` has three series, none per backend (`telemetry.py:285-289`).

**Contract.**

1. `BackendHealth.model_info: dict[str, dict]` — per served model id,
   `{context_length: int | null, quant: str | null, dim: int | null,
   resident_since: float | null}`. Every value nullable; a provider that does
   not report it yields `null`, and the UI's "windows advertises 32k while the
   pool is 128k" warning simply does not fire.
2. Population is per-provider and unequal, and the spec must say so rather than
   promise uniformity. Ollama's `/api/show` returns context length and
   quantisation and `/api/ps` returns residency; LM Studio and vLLM expose
   different shapes; a `custom` OpenAI-compatible endpoint exposes none.
   **This was not verified against provider docs while writing this spec** —
   treat the per-provider mapping as an open research item for
   `netllm_discovery/local.py`, sized separately.
3. `context_length` additionally appears on each `/v1/models` row when known,
   as the max across backends serving that id.
4. Per-backend probe history: a ring buffer in `TelemetryService`,
   `history.backend_health: {backend_id: [{ts, online, latency_ms}]}`, appended
   by the health probe (`pool.is_healthy`), not by requests. Bounded by backend
   count × 60 entries; drop the series when a backend leaves the pool.
5. `POST /netllm/v1/admin/backends/{id}/probe` → the fresh `BackendHealth` for
   one row. The Backends page's "Probe again" currently triggers a full
   `GET /netllm/v1/status?probe=1&probe_peers=1&scan=1`, which re-probes
   everything; scoping it is the point.

**Surfaces.** web: Models context/quant columns and the mismatch warning strip,
Backends probe sparkline and per-row re-probe. swift: Backends detail. cli:
`netllm models --verbose` context column. No config field.

**Tests.**
- unit `tests/test_local_discovery.py`: a stub provider returning a context
  length populates `model_info`; a provider returning nothing yields `{}` and
  never raises.
- unit: `model_info` is dropped for models no longer served, so a shrinking
  backend does not accumulate ghosts.
- unit `tests/test_telemetry.py`: `history.backend_health` is bounded and a
  removed backend's series is evicted.
- contract: `test_telemetry_contract.py` key set + `docs/telemetry-api.md` row
  for `history.backend_health`; `routes.json` regenerated for the probe route;
  `route-auth-gates.json` records it as admin.
- e2e: Models page shows a Context value for the stub backend when the stub
  advertises one, and `—` (not `0`) when it does not.

**Cost: M**, plus an unsized research item for the per-provider mapping.

**Risk.** (i) The probe currently costs one `/v1/models` call; `model_info`
needs a **per-model** call on some providers, turning one request into N. That
must be rate-limited and cached hard (a model's context length does not change
between probes), or discovery on a 40-model Ollama host becomes a thundering
herd every `health_ttl_s`. (ii) `history.backend_health` grows with backend
count; cap total series. (iii) Residency (`resident_since`) is the weakest
signal here and only Ollama-ish providers have it — it should not be a
precondition for shipping the rest.

---

### UI-6 — Structured doctor

**Closes** (5): doctor *passed-check inventory*, *per-finding severity*,
*machine-readable remediation*, *conflicting agent process control*; (doctor
*run timestamp* is `UI-3`).

**Current.** `doctor_payload` (`netllm_agent/admin.py:50-…`) appends prose into
`issues: list[{title, fix}]` and `notes: list[str]`. A passing check leaves no
trace, there is no severity, and `fix` is prose — the Doctor page matches
finding text against a regex table to decide which button to offer. The
port/pid conflict is detected only by the CLI serve lock
(`netllm_discovery.agent_lock`) and never reaches the payload.

**Contract.**

```json
{
  "checked_at": 1786786400.0,
  "checks": [
    { "id": "swarm.token_but_open_inference",
      "title": "Cluster token is set but inference is open to the LAN",
      "ok": false,
      "severity": "error",
      "detail": "agent.listen is 0.0.0.0:11400 …",
      "fix": "Set swarm.require_token_for_inference = true",
      "action": { "kind": "config_patch", "label": "Restrict to local only",
                  "endpoint": "/netllm/v1/admin/config", "method": "POST",
                  "params": { "swarm": { "require_token_for_inference": true } } } }
  ],
  "issues": [ … ], "notes": [ … ]
}
```

- Every check `doctor_payload` runs emits a row, passing or failing. `issues`
  and `notes` stay, derived from `checks`, so `netllm doctor` and any older
  client keep working; they get a deprecation-clock row.
- `id` is a stable dotted string. It is the join key for `action` and the thing
  a support bundle can be diffed on.
- `severity` is `"error" | "warn" | "info"`. Today's `issues` map to `error`,
  `notes` to `warn`.
- `action.kind` is a closed set: `config_patch`, `admin_post`, `navigate`,
  `none`. `config_patch` and `admin_post` name an existing admin route and
  carry the body — **the server does not gain a generic "apply this fix"
  executor.** A `POST /netllm/v1/admin/doctor/fix {issue_id}` that runs
  server-chosen remediations is a privilege-escalation shape: it turns one
  admin route into an open-ended one whose effect is decided by server code the
  client cannot inspect. Declaring the patch and letting the client POST it to
  the route it already has is strictly safer and needs no new endpoint.
- New check: `agent.port_conflict`, carrying `{pid, started_at, supervised}`
  from `netllm_discovery.agent_lock`, `severity: "error"`, `action.kind:
  "none"` — killing another process from a browser POST is not in scope, and
  the CLI already owns that (`netllm serve` lock handling). The design's "Stop
  pid 4821 and hand over" button is a macOS-app/CLI action; see §5.

**Surfaces.** web: Doctor page passed-list, severity dots, per-finding action
button. swift: doctor panel. cli: `netllm doctor` prints passed checks under
`--verbose`.

**Tests.**
- unit `tests/test_doctor_*.py`: a healthy config emits N checks, all `ok:
  true`, `issues == []`; each failing condition flips exactly one check.
- unit: `issues` remains byte-identical to today's output for a given config —
  the derivation must not change the legacy shape.
- unit: every `action` with `kind in {config_patch, admin_post}` names a route
  present in `tests/contract/routes.json` (assert it as a set intersection, so
  a typo'd endpoint fails CI).
- unit: `id` values are unique and stable — a frozen id roster in the test,
  same discipline as `tests/contract/test_error_taxonomy_table.py`.
- e2e: Doctor page renders "N checks · M passed", and an action button POSTs to
  the declared endpoint (assert via the page's network log).

**Cost: S–M.** Mechanical restructuring of one function plus one new check.

**Risk.** Low, provided the generic fix-executor is not built. The one real
trap: `doctor_payload` force-probes every backend and all peers
(`admin.py:97-102`), already runs in a thread (`routes/admin.py:45`), and
adding passed-check rows must not add probes.

---

### UI-7 — Cloud key lifecycle and guardrails

**Closes** (9): cloud *per-provider key verification state*, *masked key
preview* (hint half), *clear a stored key*, *placeholder-key warning*,
*keychain provenance claim*, *monthly spend ceiling*, *spend to date*,
*only when the mesh is down*, *30-day window* (with `UI-1`).
Cloud *ask before the first cloud call* is **not** here — see §5.

Split deliberately into **7a (honesty)** and **7b (guardrails)**; they have
different costs, risks and tranches.

#### 7a — key state on the wire

**Current.** `_cloud_provider_export` (`netllm_agent/admin.py:235-263`) emits
`api_key_set: bool` and nothing else about the key. `Backend.api_key` is
`exclude=True` (`models.py:605`) so no payload carries it. `buildCloudPatch`
drops a falsy pending key, so an empty box means "keep stored" and there is no
value meaning "erase". `is_netllm_placeholder_key`
(`netllm_core/source_identity.py:20`) runs server-side only.

**Contract.** Additive keys on each `config.cloud.providers[<id>]` entry:

| Key | Type | Meaning |
|---|---|---|
| `api_key_hint` | `str` | last 4 chars, `""` when unset. Never more. |
| `api_key_updated_at` | `float \| null` | epoch, stamped on write |
| `api_key_is_placeholder` | `bool` | `is_netllm_placeholder_key(resolved)` |
| `api_key_source` | `"config" \| "env" \| "keychain" \| ""` | where the resolved key came from |
| `key_status` | `"unknown" \| "ok" \| "rejected" \| "unreachable"` | last verify result |
| `key_checked_at` | `float \| null` | epoch of that verify |

Plus `POST /netllm/v1/cloud/providers/{id}/verify` → `{ok, status, checked_at,
detail}`, a real auth check (a cheap authenticated GET, per provider spec) —
distinct from `/models`, whose `status` is a side effect of listing.

Plus an erase channel: `apply_config_patch` accepts `api_key_clear: true` on a
cloud provider patch, meaning "delete the stored key". A `DELETE` route is the
alternative; the patch flag is preferable because it goes through the one
config write path that already carries the guards
(`netllm_core.config_guards`, `docs/config-guards-audit.md`).

Plus `secret_backend: "keychain" | "config_file"` on `GET /netllm/v1/status`,
so the page can state what actually happened instead of repeating the macOS
app's promise. Today a key POSTed to `/netllm/v1/admin/config` is persisted
into `config.toml` by `save_config` (`models.py:909-938`, chmod 0600); the
mockup's sentence "never written to config.toml" is false on the web surface.

**Surfaces.** web + swift both render key state and the Clear button. cli:
`netllm cloud list` shows hint + status; `netllm cloud clear-key <id>`.
`api_key_clear` is a patch verb, not a schema field — no parity obligation;
`secret_backend` is status, not config.

**Tests.**
- unit `tests/test_backend_key_redaction.py`: `api_key_hint` is at most 4 chars
  and never appears for a key shorter than 8; the full key never appears in any
  payload (that file already holds this discipline).
- unit `tests/test_admin_cloud.py`: `api_key_clear: true` erases; an omitted
  key still preserves (the existing empty-preserves contract must not break —
  that is the one regression this feature can cause).
- unit: `api_key_is_placeholder` is true for `netllm-local`.
- contract: `routes.json` + `route-auth-gates.json` for the verify route.
- e2e `tests/e2e/`: Cloud page shows the placeholder warning card for a
  provider configured with `netllm-local`, and the Clear button issues a patch
  carrying `api_key_clear`.

**Cost: S–M.** Risk: the erase path. `config_merge` treats keys as write-only
precisely so an omitted key is preserved; introducing a value that means
"delete" is exactly the shape that historically destroyed data in this repo
(F-01, `ConfigModel`'s docstring). It must be an explicit sibling flag, never
an in-band sentinel value in `api_key` itself, and it must be tested against
the macOS save path too.

#### 7b — spend ceiling, price table, mesh-offline gate

**Contract.** New `CloudConfig` fields (`models.py:475-492`):

```python
monthly_ceiling_usd: float = Field(default=0.0, ge=0.0)   # 0 = no ceiling
require_mesh_offline: bool = False
pricing: dict[str, CloudPricing] = Field(default_factory=dict)
```
with `class CloudPricing(ConfigModel): input_usd_per_mtok: float = 0.0;
output_usd_per_mtok: float = 0.0`.

Enforcement in `netllm_core/routing_policy.py:resolve_routing` — it already
owns every path to `allow_cloud_inject` (`routing_policy.py:104-190`), so both
gates belong there and nowhere else:
- `monthly_ceiling_usd`: when month-to-date spend ≥ ceiling,
  `allow_cloud_inject = False` and `cloud_leads = False`, regardless of policy
  or source opt-in. Spend must be passed in, not read — `resolve_routing` is a
  pure function of config today and must stay one; add a `cloud_spend_usd:
  float = 0.0` parameter.
- `require_mesh_offline`: suppress cloud candidates while any local or peer
  backend is healthy. "Healthy" must mean the cached health verdict, not a
  fresh probe — a probe here would put a network round trip on the request
  path.

Accounting rides on `UI-1`'s ledger: `router.cloud_spend {month_to_date_usd,
window_start, by_provider: {}}` on telemetry, and per-provider request counts
in `by_backend` joined to `Backend.cloud_provider` (`models.py:614`). Spend must
persist across restarts — `stats.json` (`telemetry.py:97-114`) is the existing
mechanism and already has a debounced atomic writer.

**Surfaces.** Three new config fields ⇒ **controls required on both the
dashboard Cloud page and macOS `CloudSettingsView.swift`**, or dated
`[[field]]` rows in `tests/conformance/ledgers/control-parity.toml`.
`cloud.pricing` is a `dict[str, BaseModel]` — the generic schema form renders
it as a `dict` widget (`config_schema.py:126-141`), which is the same
nested-object hazard that already has macOS ledger rows for
`routing.sources[].model_rewrites`; expect a `[[field]]` row for
`cloud.pricing` on macOS with an expiry. cli: `netllm cloud ceiling`,
`netllm cloud pricing set`.

**Tests.**
- unit `tests/test_cloud_routing.py`: at ceiling, `allow_cloud_inject` is
  `False` even when a policy sets `allow_cloud=true` and a source sets it too —
  the ceiling is an absolute ceiling, same shape as the `local_only` header
  (`routing_policy.py:179-185`).
- unit: `require_mesh_offline=true` with one healthy local backend suppresses
  cloud; with every backend offline it does not.
- unit: cost arithmetic against a fixed price table and known token counts,
  including a provider with no pricing row (contributes `0.0`, and the payload
  must be able to say the figure is incomplete).
- unit: spend survives a `TelemetryService` restart from `stats.json`.
- conformance `tests/conformance/kit_config_surfaces.py`: the three fields are
  hand- or schema-rendered on both surfaces, or ledgered.
- e2e: the Guardrails card writes `cloud.monthly_ceiling_usd` and the usage bar
  reads `router.cloud_spend`.

**Cost: M.** Risk: **a wrong price table silently blocks all cloud traffic.**
An unpriced provider must count as `0.0` (never blocks) rather than as an
estimate, and the ceiling must fail *open* on any arithmetic error, with the
UI able to distinguish "under ceiling" from "spend unknown". Second risk: spend
lookup on the request path — it must read a cached float updated by the ledger,
not recompute.

---

### UI-8 — Pools as a first-class object

**Closes** (11): models *pool membership rules*, *pool admission checks*,
*per-member weights and pinning*, *per-pool balancing strategy*, *sticky
sessions*, *warm residency* (control half), *drain a specific node*,
*rebalance now*, *simulate N requests*; overview *Pools as a first-class
concept*; integrations *model pool / node count*.

**Current.** `ModelPool` is `{enabled, hosts[], models[]}`
(`models.py:306-324`) — an explicit host ref list evaluated at request time,
with no rule form, no auto-join, no admission, no weights and no per-pool
strategy. Strategy is agent-wide (`routing.default_strategy`, `models.py:330`)
and `capacity_weighted` is not an implemented arm (`pool.py:585-640` implements
`failover`, `round_robin`, `least_load`, `latency_weighted`, `local_first`,
`local_spillover`, `batch_shard`). The Overview page currently groups by
`capability` from `/v1/models` — a name heuristic
(`netllm_core.capabilities.model_capability`), not a pool.

**Contract (sketch — this is the one feature that needs its own design doc).**

```python
class PoolMembership(ConfigModel):
    mode: Literal["named", "any", "pattern"] = "named"
    pattern: str = ""

class PoolAdmission(ConfigModel):
    min_context: int = 0
    max_p50_ms: float = 0.0
    uniform_quant: bool = False
    eject_after_failures: int = 0
    readmit_after_successes: int = 0
    overrides: list[str] = Field(default_factory=list)

class ModelPool(ConfigModel):        # existing fields kept
    enabled: bool = True
    hosts: list[str] = []
    models: list[str] = []
    membership: PoolMembership = …
    admission: PoolAdmission = …
    strategy: RoutingStrategy | None = None
    weights: dict[str, float] = {}
    sticky_sessions: bool = False
    min_warm_nodes: int = 0
```
Every default reproduces today's behaviour exactly: `mode="named"` is the
current host list, all-zero admission admits everyone, `strategy=None` defers
to `routing.default_strategy`.

New reads: `GET /netllm/v1/pools` → `[{id, label, primary_model, members:
[{ref, admitted, failing_checks[], weight, weight_source}], ready_nodes,
requests: {…spans}}]`. New writes: `POST /netllm/v1/admin/drain` gains an
optional `backend_id` / `agent_id` (gossiped in the heartbeat so a gateway can
drain a peer); `POST /netllm/v1/admin/route-preview {model, count, pool?}` →
`{per_backend: {}, rejected: [{reason, count}]}`, computed by the planner with
no upstream calls. `capacity_weighted` becomes a real `RoutingStrategy` arm
backed by free-VRAM (`UI-4`) and p50 (`UI-2`).

**Dependencies.** `admission.min_context` needs `UI-5`. `admission.max_p50_ms`
and weight derivation need `UI-2`. `capacity_weighted` needs `UI-4`'s VRAM.
`min_warm_nodes` needs residency *and* a preload call the agent cannot make
today. Per-pool request counts need `UI-1`. **This feature is downstream of
four others; building it first would mean building it on placeholders.**

**Surfaces.** Nine new config fields across three models ⇒ controls on the
dashboard Models page **and** macOS, or ledger rows. `PoolMembership` /
`PoolAdmission` are nested objects, which `SchemaFormView` has no widget for
(the existing macOS ledger rows for `routing.sources[].model_rewrites` and
`.match` say exactly this) — expect ledger rows and budget the SwiftUI
nested-object widget as part of the cost. cli: `netllm pools` command family.

**Tests.** Property tests on selection under weights
(`tests/test_model_resolution_property.py` has the shape); admission state
machine unit tests (eject after N, re-admit after M, override forces admit);
`tests/contract/test_candidate_schedule.py` extended so a pool strategy changes
the schedule and nothing else; sticky-session affinity determinism; conformance
parity for nine fields; e2e for the Pools page and the simulate strip.

**Cost: L.** Largest item in the set by a wide margin: new selection arm, new
config subtree, a state machine with hysteresis, a planner-only preview path,
and a nested-object widget on two clients.

**Risk.** (i) Admission adds per-request evaluation to selection, which is hot —
admission state must be recomputed on probe/health transitions and cached, not
evaluated per request. (ii) Sticky sessions introduce an affinity key with no
existing home; hashing the `user` field or a new `x-netllm-session` header
changes routing determinism and interacts with every strategy arm — it is the
part most likely to produce "why did this request go there" bugs. (iii)
`min_warm_nodes` requires issuing preload calls to providers, i.e. the router
generating traffic no client asked for; that is a genuinely new behaviour class
and should be scoped out of the first pass.

---

### UI-9 — Routing explain (dry-run resolution) and one-token test

**Closes** (7): routing *route-resolution bench*, *resolved hop chain*,
*matched-policy / why-not-local / TTFT / hop-count*, *skip reasons for
non-matching policies*, *per-strategy explainer text*; doctor *latency probe per
node*; integrations *verify connection*.

**Current.** `_resolved_routing` (`netllm_agent/service/policy.py:159-180`) is
internal to live request handling and unreachable over HTTP.
`match_routing_policy` (`netllm_core/routing_policy.py:39-57`) returns the
first match and discards why the others did not match. `x-netllm-hops`
(`models.py:91`) is a request header on live proxy traffic, never returned.
`netllm test` is CLI-only. `RoutingConfig.default_strategy` has no
`Field(description=…)`, so `config_schema._field_spec` (`config_schema.py:96-99`)
emits `options` with no per-option text.

**Contract.**

`POST /netllm/v1/routing/explain`, admin-gated:

```json
// request
{ "model": "gemma4:27b", "api_format": "openai", "source": "cursor",
  "headers": {}, "dry_run": true }
// response
{ "resolved": { "strategy": "local_spillover", "local_only": false,
                "allow_cloud_inject": false, "prefer_provider": null },
  "matched_policy": { "key": "1:local-openai", "name": "local-openai" },
  "skipped": [ { "policy_key": "0:embeddings",
                 "reason": "model_prefix 'bge-' did not match" } ],
  "cloud_consulted": false,
  "cloud_skip_reason": "allow_cloud is false on the matching policy",
  "hops": [ { "kind": "self",    "agent_id": "a1b2", "hostname": "linux" },
            { "kind": "peer",    "agent_id": "c3d4", "hostname": "mac-mini-m4" },
            { "kind": "backend", "base_url": "http://…:8080/v1",
              "provider": "omlx" } ],
  "hop_count": 1,
  "local_skip_reason": { "reason": "busy", "in_flight": 3 },
  "ttft_ms": null }
```

`dry_run: false` issues a real one-token completion and fills `ttft_ms` (from
`UI-2`). That same route, with `{count: N}`, is `UI-8`'s route-preview — one
planner, two shapes; do not build two.

`match_routing_policy` gains a sibling that returns `(matched, skipped[])`
rather than changing the hot-path signature. `resolve_routing` gains an
optional trace sink defaulting to `None`, so the live path allocates nothing.

Per-strategy explainer text: `option_help: {option: str}` on Literal field
specs in `config_schema._field_spec`, sourced from a docstring-adjacent map in
`netllm_core.routing_policy` (which already imports `RoutingStrategy` and is
where the semantics live). This is additive to the schema document; older
clients ignore it.

**Surfaces.** web: Routing page test pane, Doctor latency probe, Integrations
Verify button. swift: routing explain sheet. cli: `netllm route explain
--model … --dry-run` (`netllm test` already covers the non-dry form).
`option_help` is schema metadata, not a config field — no parity obligation.

**Tests.**
- unit `tests/test_routing_policies.py`: explain output for a fixture config
  matches the live `resolve_routing` result for the same inputs — asserted by
  calling both, so the explainer cannot drift from the router. This is the
  single most important assertion in the feature.
- unit: `skipped[]` names every non-matching enabled policy exactly once, with
  the specific predicate that failed.
- unit: `dry_run: true` issues zero upstream requests (assert with a mock
  transport that raises on use).
- contract: `routes.json` + `route-auth-gates.json`; `tests/contract/
  test_error_taxonomy_table.py` for the failure shape when the model resolves
  to nothing.
- e2e: Routing page test pane renders a hop chain for the stub backend and the
  skip footnote names a real policy.

**Cost: M.** The explainer itself is small; keeping it provably identical to the
live resolver is the work.

**Risk.** The failure mode is an explainer that diverges from the router — a
debugging tool that lies is worse than none. Mitigation is structural: one
resolver with an optional trace sink, never a second implementation. Secondary:
`dry_run: false` sends real traffic and real tokens from an admin route; it
must respect `local_only`, must be rate-limited, and must never be reachable
without admin access.

---

### UI-10 — Mesh and backend lifecycle actions

**Closes** (9): network *forget a discovered peer*, *save & restart*, *QR join
code*; peers *invite a machine*, *ignore/deny a discovered agent*, *gateway
role handover*, *peer join state / rejection reason*; overview *hand over
role*; backends *per-backend enable/disable one-click*.

**Current.** There is no peer eviction (`swarm.peers` only pins peers *in*), no
denylist, no restart endpoint (`POST /netllm/v1/admin/config` returns
`needs_restart: true` and stops), no role transfer (`agent.role` is config,
`models.py:396`), and `peers_scan_payload` drops probes that failed auth, so a
token-mismatched agent is invisible rather than listed.

**Contract.**
- `swarm.ignored_peers: list[str] = []` — a new config field, honoured by the
  mDNS merge and the subnet scan. This is the denylist; `POST /netllm/v1/admin/
  peers/forget` writes to it rather than mutating in-memory state, so "Forget"
  survives the next heartbeat (a purely in-memory eviction reappears in
  `peer_stale_after_s`, which is the bug the design's button implies).
- `peers_scan_payload` keeps unauthorised hits as
  `{listen_url, agent_id?, join_state: "rejected", reason: "token_mismatch",
  probed_at}`. Leaks nothing an unauthenticated scanner could not learn itself.
- `POST /netllm/v1/admin/restart` — graceful re-exec honouring drain. Only
  meaningful when the agent is supervised (launchd / systemd / the macOS app);
  under `netllm serve` in a terminal it must refuse with a clear error rather
  than exiting and never coming back. `netllm_discovery.agent_lock` and the
  supervised-port doctor tests (`tests/test_doctor_supervised_port.py`) are the
  existing knowledge of that distinction.
- `POST /netllm/v1/admin/role {role, hand_to}` — demote self, promote target,
  gossip. Requires the target to accept; a one-sided demotion leaves a mesh with
  no gateway.
- Join ticket: `POST /netllm/v1/admin/join-ticket` → `{url, token, expires_at}`,
  a short-lived single-use pairing string. **Pull, not push** — see §5 on why
  "Send invite" should not exist.
- Per-backend enable: no new endpoint. The Backends page synthesises a
  `routing.backends` row and POSTs `/netllm/v1/admin/config`, which is what the
  gap line already concluded.

**Surfaces.** `swarm.ignored_peers` is a config field ⇒ dashboard + macOS
control or ledger rows. The rest are actions ⇒ `ControlDescriptor` entries with
`admin_route` set and `surfaces_required` chosen per action (restart and role
handover want `cli` too).

**Tests.** Unit per route; `routes.json` + `route-auth-gates.json` for four new
admin routes; a two-agent test that a forgotten peer does not return after a
heartbeat cycle; a test that `restart` refuses when unsupervised; conformance
parity for `ignored_peers`; e2e for the Peers/Network buttons.

**Cost: M** overall; `restart` and `role` are each independently risky enough to
land separately.

**Risk.** (i) `restart` can strand an agent — the highest-blast-radius item in
this spec. It must refuse unless it can prove something will restart it. (ii)
Role handover has a split-brain failure (two gateways, or none) with no
consensus mechanism in the codebase; it needs an explicit "target must ack
before self demotes" ordering and a timeout that leaves the *current* gateway
in place. (iii) `join-ticket` mints a credential; single-use, short TTL, and it
must never be logged.

---

### UI-11 — Logs as a stream

**Closes** (5): logs *structured log records*, *per-node log scope*, *total line
count and paging*, *download the log file*, *reveal log folder*; preferences
*reveal log directory*.

**Current.** `logs_payload` (`netllm_agent/admin.py:581-596`) returns
`{log_dir, log_file, exists, size_bytes, tail[], truncated}` where `tail` is raw
formatter text, capped at 2000 lines, this host only. The page regex-parses it
and the 10 s poll refetches a fixed `?tail=200`, clobbering any wider fetch.

**Contract.**
- `GET /netllm/v1/logs?format=json` → adds `records: [{ts, level, logger,
  message}]` alongside `tail` (never instead of — `tail` is what the macOS app
  and CLI read). Parsing happens server-side, where the format string lives.
- `total_lines: int` in the payload, and `?before=<line_no>` for cursor paging
  that survives the poll.
- `GET /netllm/v1/logs/download` streaming `agent.log` as `text/plain` with
  `Content-Disposition`. Admin-gated; it is the whole log, secrets and all.
- `?node=<agent_id>` proxying a peer's tail. Requires the peer to accept an
  authenticated admin read — i.e. it only works in a token-configured mesh, and
  must say so rather than silently returning this host's log.
- Reveal-folder: **not built.** See §5.

**Surfaces.** web (all of it), swift (structured records), cli (`netllm logs`
unchanged). No config field.

**Tests.** Unit: a log line in the current format round-trips to the right
four fields, and a line that does not match is emitted with `level: null` and
the raw text as `message` (never dropped — a stack trace is exactly the thing
that will not match). Unit: `?before=` paging returns disjoint, ordered pages.
Unit: `?node=` against an unreachable peer returns an error, not local content.
Contract: `routes.json` for the download route. e2e: Logs page shows level
columns and "Load more" fetches older lines without the poll clobbering them.

**Cost: S–M.**

**Risk.** (i) The download route emits an unredacted log; it must be
admin-gated and should be named in `SECURITY.md`. (ii) `?node=` forwards admin
credentials between agents — reuse the existing peer auth header path
(`SwarmRegistry._auth_headers`, `swarm.py:250`), do not invent a second one.
(iii) Server-side parsing of an unbounded log must stay bounded by `tail`.

---

### UI-12 — App-shell preferences

**Closes** (6): preferences *login item toggle*, *release channel*, *log level
control*, *support bundle*, *config file path + open/export/reset*, *build
number*; (the *update last-checked timestamp* is `UI-3`; *reveal* and *native
picker* are §5).

**Current.** `UiConfig` (`models.py:435-445`) has ten fields, none of which is a
log level, a release channel or a login item. `serve` hard-codes
`log_level="info"` in its `uvicorn.run` call
(`packages/netllm-cli/src/netllm_cli/commands/serve_lifecycle.py:272`).
`fetch_latest_release`
always hits `/releases/latest`, so prereleases are reported but never
requested. `version_payload` returns `"build": None` unconditionally
(`netllm_core/update.py:471-478`). No payload carries the config file path and
there is no reset endpoint.

**Contract.**
- `ui.log_level: Literal["warn","info","debug"] = "info"`, honoured by `serve`
  and by the agent's own logger (hot-appliable via `logging.getLogger("netllm").
  setLevel`, so it does not need a restart).
- `ui.release_channel: Literal["stable","alpha"] = "stable"`, with a
  prerelease-aware `fetch_latest_release`.
- `ui.open_at_login: bool = False` — config only; the actual `SMAppService`
  registration is macOS-app work.
- `config_path: str` in `version_payload()` (it is already the natural home:
  the CLI, dashboard and app all read that payload).
- `build`: a real commit/build identifier stamped at package time. The stamping
  belongs in the packaging step (`packaging/`), read by `version_payload`.
- `POST /netllm/v1/support-bundle` → a zip of redacted config + doctor output +
  last 2000 log lines. Redaction is the whole feature: it must reuse the
  existing write-only/secret field metadata (`json_schema_extra={"write_only":
  True}`, `models.py:109-111,149-151,257-259,467-469`) to decide what to strip,
  not a hand-written key list, or the next secret field added leaks.
- `POST /netllm/v1/admin/config/reset` — rewrites the config to defaults after
  taking a backup via the existing `pre_migration_backup` mechanism
  (`models.py:818-866`).

**Surfaces.** Three new `UiConfig` fields ⇒ dashboard + macOS controls or ledger
rows. `ui.*` is already rendered generically on both surfaces
(`renderSchemaForm("ui")` on the web, `SchemaFormView(fields: uiFields)` on
macOS), so these three land for free once `UI-0` re-anchors the region — this is
the cheapest parity story in the set. cli: `netllm support-bundle`,
`netllm config reset`.

**Tests.** Unit per field; unit that the support bundle contains no value from
any `write_only` field, driven off `model_fields` so a new secret is covered
automatically; unit that `config reset` writes a backup first; conformance
parity for three fields; `tests/test_packaging_version.py` for the build stamp;
e2e for the Preferences page.

**Cost: S** for the config fields and `config_path`/`build`; **M** with the
support bundle and reset.

**Risk.** `config/reset` destroys a working configuration from a browser
button; it must take a backup unconditionally and must be behind a typed
confirmation on both clients. The support bundle is a redaction bug waiting to
happen — derive the redaction from field metadata, and assert it.

---

## 3. Tranches

### Tranche 1 — honesty

The UI currently shows `—`, an empty state, a mislabelled window, or (twice) a
wrong number, for data a user reasonably expects. Highest ratio, and two items
here are corrections rather than features.

| # | Feature | Cost | Why first |
|---|---|---|---|
| 1 | `UI-0` re-anchor Axis D | S | Blocks every config field below; the parity gate is currently stale against this branch |
| 2 | `UI-3` epoch timestamps | S | Eight additive fields, no logic, unblocks every "N ago" in the design |
| 3 | `UI-2` real TTFT + percentiles | M | `avg_prefill_tps` is a hardcoded constant on screen today. Fixing a wrong number outranks adding a missing one |
| 4 | `UI-1` windowed ledger | M | One capability behind 13 gaps; without it the design's entire time vocabulary is unbacked |
| 5 | `UI-6` structured doctor | S–M | Turns a regex-matched prose list into a check inventory; no new probes, no new executor |
| 6 | `UI-7a` cloud key state | S–M | The Cloud page currently cannot distinguish a real key from `netllm-local`, and states a keychain promise that is false on the web |
| 7 | `UI-11` structured logs (records, `total_lines`, download) | S–M | Deletes a client-side regex parser; `?node=` defers to tranche 2 |
| 8 | `UI-4a` heartbeat: `discovered_via`, `also_reachable_at`, `providers`, `authenticated` | M | Peers/Network "Source" columns exist and read `heartbeat` for everything. Excludes GPU/VRAM |
| 9 | `UI-12a` `config_path`, `build`, `ui.log_level`, `ui.release_channel` | S | Four small fields; `ui.*` is generically rendered on both surfaces already |

Tranche 1 closes roughly 45 of the 94 gaps.

### Tranche 2 — capability

Genuinely new function. Each is defensible on its own; none is a prerequisite
for tranche 1.

| Feature | Cost | Note |
|---|---|---|
| `UI-9` routing explain + one-token test | M | Highest debugging value per line in the whole set; also the only way to answer "why did this go there" today |
| `UI-7b` cloud ceiling, pricing, mesh-offline gate | M | The one feature that prevents a bill. Must fail open |
| `UI-5` probe enrichment (context, quant, probe history, scoped re-probe) | M + research | Per-provider mapping unverified; context length alone is most of the value |
| `UI-10` mesh lifecycle (forget/ignore, join state, join ticket) | M | Ship `restart` and `role handover` separately and later; both can strand a mesh |
| `UI-4b` GPU / VRAM in the heartbeat | L | Per-platform, low confidence, high mockup salience |
| `UI-12b` support bundle, config reset | M | Support bundle earns its keep the first time someone files a bug |

### Tranche 3 — deferred, with the argument

**`UI-8` pools as a first-class object.** The single largest item, and it is
downstream of four others (`UI-1` counts, `UI-2` latency, `UI-4` VRAM, `UI-5`
context/quant). Building it now means building admission checks against
placeholder inputs and a `capacity_weighted` strategy against a VRAM field that
is `null` on every platform. It also carries the two riskiest sub-items in the
spec — per-request admission evaluation on the hot path, and sticky-session
affinity, which changes routing determinism for every strategy arm. Defer until
its four dependencies have shipped and produced real data, then spec it
properly on its own. In the meantime the Models page should say pools are
host-scoped catch-alls (which is what `ModelPool` is, `models.py:306-324`) and
stop drawing membership rules, admission gates and derived weights.

**`min_warm_nodes` / keep-warm (inside `UI-8`).** Requires the router to issue
preload requests no client asked for — a new behaviour class with its own cost,
failure and billing questions on cloud backends. Not worth it before pools
exist.

**`POST /netllm/v1/admin/restart` and role handover (inside `UI-10`).** Restart
can leave an unsupervised agent dead; role handover has a split-brain failure
with no consensus primitive anywhere in the codebase. The user-visible cost of
*not* having them is one manual restart and one config edit. Defer.

**`cloud.confirm_first_call`.** Not deferred — rejected. See §5.

**`?node=<peer_id>` log proxying (inside `UI-11`).** Only works in a
token-configured mesh, forwards admin credentials between agents, and the same
information is one click away in the peer's own dashboard. Low value, real
surface area.

**Per-request scenario/policy cross-tabs beyond the specified dimensions.**
`UI-1` deliberately stops at four dimensions plus a bounded top-N. Anything
finer is attacker-controlled cardinality on the hot path for a chart nobody
asked for.

---

## 4. Sizing summary

| Feature | Cost | Hot path? | New config fields | New routes |
|---|---|---|---|---|
| UI-0 | S | no | 0 | 0 |
| UI-1 | M | **yes** | 0 | 0 |
| UI-2 | M | **yes** | 0 | 0 |
| UI-3 | S | no | 1 (read-only) | 0 |
| UI-4 | M / L | no | 0 | 1 |
| UI-5 | M | no | 0 | 1 |
| UI-6 | S–M | no | 0 | 0 |
| UI-7a | S–M | no | 0 | 1 |
| UI-7b | M | **yes** (gate) | 3 | 0 |
| UI-8 | L | **yes** (admission) | 9 | 2 |
| UI-9 | M | no (trace sink is opt-in) | 0 | 1 |
| UI-10 | M | no | 1 | 4 |
| UI-11 | S–M | no | 0 | 1 |
| UI-12 | S / M | no | 3 | 2 |

Every "new route" row implies regenerating `tests/contract/routes.json` via
`scripts/generate-routes-json.py` and adding a `tests/contract/
route-auth-gates.json` entry; every "new config field" implies a control on the
dashboard **and** the macOS app, or a dated row in
`tests/conformance/ledgers/control-parity.toml`. Every telemetry key implies a
row in `docs/telemetry-api.md` — `tests/contract/test_telemetry_contract.py`
asserts that in both directions.

---

## 5. Gaps to close by changing the design, not the backend

Ordered by how strongly the design is wrong.

**1. Routing — "Require same model for shard" toggle.** The mockup draws a
switch for `routing.require_same_model_for_shard`, which is deprecated and
inert: its only consumer (`plan_batch_shard`) was deleted, it is removed from
`config_summary`, the dashboard and macOS Settings as of 0.4.6, and
`netllm_core.deprecations` schedules its removal in **0.6.0**
(`models.py:332-338`). Building a control for it would mean re-adding a field
to two surfaces months before deleting it. **Delete the toggle from the
mockup.**

**2. Cloud — "Ask before the first cloud call".** The design draws a
confirmation gate in the request path. There is no channel to ask on: the
caller is an SDK inside an editor, and blocking the completion until a human
clicks a dashboard notification means the request sits until the client's read
timeout. The honest control is a *ceiling* that refuses (`UI-7b`) plus a
*post-hoc* notification — not a pre-flight prompt. **Replace the toggle with
"Notify me the first time a request goes to cloud", implemented client-side off
`router.windows.by_backend` and `Backend.cloud_provider`, and keep the refusal
in the ceiling.**

**3. Cloud — the keychain provenance sentence.** "Keys live in the system
keychain and are injected into the agent process only — never written to
`config.toml`" is true of the macOS app (`KeychainStore.swift`) and false of the
dashboard: a key POSTed to `/netllm/v1/admin/config` is persisted by
`save_config` (`models.py:909-938`). The fix is not to build keychain storage in
the agent; it is to make the sentence data-driven off `secret_backend`
(`UI-7a`) and say what actually happened on this surface. **Rewrite the copy.**

**4. Peers / Network — "Send invite" and the QR that carries a token.** Pairing
in netllm is a pull: `netllm join` runs on the joining machine with a cluster
token. An "invite" that pushes a credential outward to an address discovered by
a subnet scan is a credential-leak primitive — the recipient is by definition
not yet authenticated, and the scan may have found something that is not ours.
**Change the design to a join ticket the operator copies or shows as a QR
(`UI-10`), and delete the per-row "Send invite" action.**

**5. Logs / Preferences — "Reveal folder", "Change… (directory picker)".** A
browser cannot open a file manager, and `POST /netllm/v1/admin/reveal-path` is
an agent-side "open a window on the host" primitive whose only legitimate caller
is a native app running on that same host. **These are macOS-app affordances;
the web page should copy the path (which it already does). Draw them only in
the macOS mockup.**

**6. Doctor — "Stop pid 4821 and hand over".** Killing another process from a
browser POST is a category of power the admin surface should not acquire for
one button. The conflict *detection* is worth shipping (`UI-6`'s
`agent.port_conflict` check); the remediation belongs to the CLI and the macOS
app, which already own the serve lock. **Keep the finding, change the web
action to "Show me how" with the command.**

**7. Backends — "+ Add server" header action.** The same page's Manual
overrides panel already adds rows. A second entry point would fork two config
paths (`discovery.custom_endpoints` vs `routing.backends`) that behave
differently. **Drop the header button, or make it scroll to the existing
panel.**

**8. Backends — "Stop probing" / per-row enable on a *discovered* backend.**
A discovered backend has no `BackendOverride` row, so the toggle must
synthesise one — which silently converts a discovered endpoint into a
hand-authored config entry that no longer tracks discovery. That is a real
semantic change hiding behind a switch. **Either draw it as "Pin and disable"
so the consequence is visible, or move it into the overrides panel.**

**9. Overview — the "mesh total" sparkline polyline.** Peer rates can only
arrive at heartbeat cadence (`swarm.heartbeat_interval_s`, default 10 s,
`models.py:156`), so a mesh-total line drawn at the same resolution as the local
1 s series would interpolate 10× more detail than exists. **Draw mesh total as a
coarser stepped series, or label its resolution.**

**10. Routing — the drag handle.** Order already persists as `routing.policies`
array order; the redesign implemented ↑/↓ buttons because a pointer-only drag
handle is not keyboard reachable. **Keep the buttons; a drag affordance is
additive, never a replacement.** No backend work either way.

**11. Network — "Revert".** Pure dashboard chrome: `state.config` is already the
saved baseline. Not a gap. **No backend work.**

**12. Integrations — "Write to shell profile".** The browser is not necessarily
on the machine that runs the client, and an agent endpoint that appends to
`~/.zshrc` on POST is a remote-write primitive with a persistence side effect.
`netllm connect` already does this on the right machine. **Drop from the web
surface; keep on CLI and macOS.**

---

## 6. Gap → disposition index

All 94 lines. `§5` means "close by changing the design".

**backends.md (7)** — per-node VRAM/GPU `UI-4b`+`UI-5` · last-probe wall clock
`UI-3` · per-backend p50 `UI-2` · probe history sparkline `UI-5` ·
per-backend re-probe `UI-5` · stop probing / per-row enable `§5.8` ·
"+ Add server" `§5.7`

**cloud.md (10)** — monthly ceiling `UI-7b` · ask before first cloud call
`§5.2` · only when mesh is down `UI-7b` · spend to date `UI-7b` · 30-day window
`UI-1`+`UI-7b` · key verification state `UI-7a` · masked hint + stored-at
`UI-7a`+`UI-3` · clear a stored key `UI-7a` · placeholder-key warning `UI-7a` ·
keychain provenance `§5.3`+`UI-7a`

**doctor.md (6)** — passed-check inventory `UI-6` · per-finding severity `UI-6`
· machine-readable remediation `UI-6` · run timestamp `UI-3` · latency probe per
node `UI-9` · conflicting agent process `UI-6` (detection) + `§5.6` (action)

**integrations.md (10)** — connected clients by user-agent `UI-1` · API surface
per client `UI-1` · model most used per client `UI-1` · rolling 24h window
`UI-1` · per-client live indicator `UI-1` · model pool / node count `UI-8` ·
verify connection `UI-9` · write to shell profile `§5.12` · LAN address when
bound to loopback `UI-3`-adjacent (`status.lan_ip` from
`netllm_discovery.lan.local_lan_ip`, `lan.py:73` — ship with `UI-3`) ·
scenario label flattening `UI-1`

**logs.md (5)** — structured records `UI-11` · per-node scope `UI-11` (tranche 3)
· total lines + paging `UI-11` · download `UI-11` · reveal folder `§5.5`

**models.md (12)** — per-node context window `UI-5` · windowed per-pool metrics
`UI-1`+`UI-2` · per-node quantisation `UI-5` · membership rules `UI-8` ·
admission checks `UI-8` · weights and pinning `UI-8` · per-pool strategy `UI-8` ·
sticky sessions `UI-8` · warm residency `UI-5` (observe) + `UI-8` (control,
deferred) · drain a specific node `UI-8` · rebalance now `UI-8` · simulate N
requests `UI-9` (same planner route)

**network.md (7)** — last provider scan time `UI-3` · cluster token provenance
`UI-3`+`UI-4a` · forget a discovered peer `UI-10` · peer discovery source
`UI-4a` · save & restart `UI-10` (deferred) · QR join code `UI-10` ·
revert `§5.11`

**overview.md (10)** — p50 TTFT `UI-2` · live tok/s `UI-1`+`UI-2` · 5-minute
traffic window `UI-1` · mesh-total throughput series `UI-4a`+`§5.9` ·
recent-event strip `UI-4a`-adjacent (`GET /netllm/v1/events`; not separately
specified — fold into `UI-4` or drop) · per-node GPU/VRAM `UI-4b` · peer
provider + model count `UI-4a` · pools first-class `UI-8` · per-pool traffic
share `UI-1` · hand over role `UI-10` (deferred)

**peers.md (10)** — discovery mechanism `UI-4a` · live per-peer share `UI-1` ·
join state / rejection reason `UI-10` · invite a machine `§5.4`+`UI-10`
(ticket) · ignore/deny `UI-10` · gateway role handover `UI-10` (deferred) ·
per-peer detail drill-in `UI-4a` · last full scan timestamp `UI-3` · heartbeat
age for scan rows `UI-3` · also_reachable_at `UI-4a`

**preferences.md (9)** — login item `UI-12` · update checked_at `UI-3` ·
release channel `UI-12` · log level `UI-12` · support bundle `UI-12` (tranche 2)
· reveal log directory `§5.5` · native directory picker `§5.5` · config path +
reset `UI-12` · build number `UI-12`

**routing.md (8)** — per-policy match counts `UI-1` · route-resolution bench
`UI-9` · resolved hop chain `UI-9` · matched-policy/why-not-local/TTFT/hops
`UI-9`+`UI-2` · skip reasons `UI-9` · per-strategy explainer text `UI-9` ·
drag-to-reorder `§5.10` · require-same-model-for-shard toggle `§5.1`

One gap — overview *recent-event strip* — is the only line in the set with no
clean home. It wants an event log (`peer_up`, `peer_down`, `backend_down`, with
duration and re-route counts) that nothing in the agent records today. It is
plausible as a small ring buffer written from the same health/heartbeat
transitions that `UI-4` and `UI-5` already touch, but the "3 requests re-routed"
figure needs failover attribution that `AttemptRecorder` does not currently
keep. **Recommend: ship the peer/backend up-down events in `UI-4` and drop the
re-route count from the design until failover attribution exists.**

---

## 7. Open questions

- The per-provider mapping for `UI-5` (`context_length`, `quant`, residency per
  provider API) is **unresearched**. Ollama almost certainly has it; LM Studio,
  vLLM and `custom` are unknown. Size `UI-5` only after that is checked.
- Whether the macOS app should carry `UI-1`'s windowed figures at all, or take a
  `[[control]]` ledger row alongside the existing `serving` entry (which expires
  `phase-8`), is a product call this spec does not make.
- GPU/VRAM (`UI-4b`) has no identified cross-platform source. If none is found,
  the honest outcome is to delete the VRAM column from the Backends mockup
  rather than ship a permanently-`null` field.
