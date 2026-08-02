# 03 · Request lifecycle and routing

## Architecture: one engine, four surface adapters

There is **exactly one failover loop** in the codebase. Until the F-24/F-25/F-26
consolidation there were five — one hand-copied into each proxy method — and the
divergences between them are catalogued cell-by-cell in
[refactor/behavior-matrix.md](refactor/behavior-matrix.md). They are gone.

```mermaid
flowchart TD
    subgraph EDGE["app.py routes"]
        R1["POST /v1/chat/completions"]
        R2["POST /v1/responses"]
        R3["POST /v1/embeddings"]
        R4["POST /v1/messages"]
    end

    subgraph PLAN["decide once, before any await"]
        BP["build_request_plan()<br/>source · scenario · model · routing<br/>shard · headers · immutable payload<br/>capability guard · admission"]
    end

    subgraph ADAPT["service/surfaces/ — the whole per-surface variance budget"]
        A1["ChatAdapter"]
        A2["ResponsesSurfaceMixin<br/>(edge translation over ChatAdapter)"]
        A3["EmbeddingsAdapter"]
        A4["MessagesAdapter"]
    end

    subgraph ENG["service/engine.py — the only failover loop"]
        E1["run_with_failover()<br/>non-streaming"]
        E2["open_stream() → StreamSession<br/>streaming"]
        E3["AttemptRecorder<br/>the only accounting writer"]
    end

    R1 --> BP
    R2 --> BP
    R3 --> BP
    R4 --> BP
    BP --> ADAPT
    A1 --> ENG
    A2 --> A1
    A3 --> ENG
    A4 --> ENG
    E1 --> E3
    E2 --> E3
```

| Route | Adapter | Translation |
|-------|---------|-------------|
| `POST /v1/chat/completions` | `ChatAdapter` (`surfaces/chat.py`) | none (native) |
| `POST /v1/responses` | `ResponsesSurfaceMixin` over `ChatAdapter` | Responses ⇄ chat at the edge, then the chat path verbatim |
| `POST /v1/embeddings` | `EmbeddingsAdapter` (`surfaces/embeddings.py`) | none; Anthropic-format rows are schedule-ineligible |
| `POST /v1/messages` | `MessagesAdapter` (`surfaces/messages.py`) | Messages ⇄ chat per selected backend; Anthropic-native rows called directly |

The Responses surface exists because Codex CLI removed Chat Completions support for custom
providers (Feb 2026); netllm translates once at the edge so every downstream behaviour —
source identity, per-source routing, scenarios, capacity, failover — is shared.

### The three seams

**`RequestPlan`** (`netllm_agent/request_plan.py`) is built once, before the
route's first `await`: resolved source, classified scenario, canonical model
(rewrites then scenario override), resolved routing, shard context, normalized
headers, and the immutable payload. Admission (`sources[].max_concurrency`) is
taken here, which is why a `429` or a capability `400` reaches a streaming
client as a real HTTP status instead of a 200 with an aborted body — the
pre-flight/stream split is structural, not a convention.

**`CandidateSchedule`** (`netllm_agent/candidates.py`) is what the adapter hands
the engine: a primary candidate set, `extra_candidates` (request-scoped cloud
rows), ordered `fallback_tiers`, `ineligible_ids` (dialect eligibility — *not* a
failure record), and an explicit `max_attempts` that sums over **everything**.
The old `max(len(pool.backends), 1)` counted pool rows only, so messages could
silently exceed its own cap through the fallback tier while chat could not.

**`SurfaceAdapter`** (`surfaces/base.py`) is a Protocol, and it is the entire
per-surface variance budget: `guard`, `candidates`, `build_invocation`,
`invoke`/`invoke_stream`, `extract_usage`, `restore_model`/`restore_stream_line`,
`classify_error`, `exhaustion_error`, `mid_stream_error_frame`, `wire_error`.

**Anti-erosion gate** (`scripts/check-engine-erosion.py`, run in CI and pinned by
`tests/contract/test_engine_erosion.py`): `engine.py` may not reference a
`Surface` member, import any `surfaces/*` module except `base`, or branch on
`plan.surface` or an adapter's concrete type. A new per-surface need extends the
protocol. This is what makes "one loop" a property that holds over time rather
than one that held on the merge date.

## End-to-end sequence (non-streaming chat)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant API as app.py route
    participant S as AgentService
    participant SI as source_identity
    participant SC as scenarios
    participant RP as routing_policy
    participant P as RouterPool
    participant U as OpenAIUpstream
    participant B as Backend

    C->>API: POST /v1/chat/completions
    API->>API: require_inference_access()
    API->>S: build_request_plan(Surface.CHAT, payload, headers)
    S->>SI: resolve_source(headers, routing.sources)
    SI-->>S: ResolvedSource(id, resolved_via, authenticated)
    S->>SC: classify_scenario(payload, api_format, UA)
    SC-->>S: long_context | web_search | think | background | default
    S->>S: model_rewrites → scenario.model → adapter.guard() [400]
    S->>RP: resolve_routing(globals, policies, source, scenario, headers)
    RP-->>S: ResolvedRouting(strategy, local_only, allow_cloud_inject,<br/>prefer_provider, pinned_backend, cloud_leads, allowlist)
    S->>S: _source_admit() → 429 if over cap [before first await]
    S-->>API: RequestPlan (frozen)
    API->>S: engine.run_with_failover(ChatAdapter, plan)
    S->>S: refresh_local_backends()  [10 s TTL scan + peer merge + prune]
    opt routing.allow_cloud_inject
        S->>S: adapter.candidates() — inject env-key cloud row as<br/>extra_candidates + materialize [cloud.providers.*]
    end
    loop attempt ≤ schedule.max_attempts
        S->>P: select_backend(model, strategy, exclude=tried ∪ ineligible_ids, ...)
        P-->>S: Backend | None
        S->>P: acquire(backend)
        S->>U: adapter.invoke(Invocation{model: resolver.upstream_model()})
        alt success
            U-->>S: response
            S->>P: AttemptRecorder.success → mark_success, REQUESTS_TOTAL,<br/>latency, tokens, telemetry, shard ledger
            S-->>C: response (adapter.restore_model → requested name)
        else adapter.classify_error(exc)
            U-->>S: error
            S->>P: AttemptRecorder.failure → mark_failure(capacity=is_capacity_error)
            S->>S: tried.add(backend.id) → next attempt
        end
        S->>P: release(backend)
    end
    S-->>C: adapter.exhaustion_error(plan, last_error)
    Note over S: _source_release() in run_with_failover's finally —<br/>success, exhaustion, unclassified raise and cancellation alike
```

Streaming follows the same two-phase walk through `open_stream`, whose
per-attempt unit is **select → acquire → connect → first event**: it returns a
`StreamSession` only once an upstream stream has actually produced its first
event, so everything that could still pick a different backend has happened
before the caller holds a session. `StreamSession` then owns per-line model
restore, usage capture, success accounting, shard completion, the `yielded_any`
no-replay rule, and release — on clean end, on mid-stream failure, and on client
disconnect (`GeneratorExit`) alike. It reads one chunk ahead of its consumer, so
a consumer that stops at its own terminator (the Responses bridge breaks at
`data: [DONE]`) can no longer strand the accounting.

## Routing decision flow

```mermaid
flowchart TD
    START([request]) --> SRC["resolve_source()<br/>header → netllm-&lt;id&gt;[.secret] key → User-Agent → 'default'"]
    SRC --> SCEN["classify_scenario()<br/>long_context &gt; web_search &gt; think &gt; background &gt; default"]
    SCEN --> REW["model_rewrites → scenario.model"]
    REW --> CAP{"adapter.guard():<br/>model_capability matches surface?"}
    CAP -->|no| E400["400 — dialect-typed error body.<br/>chat/messages: cannot serve chat.<br/>embeddings: cannot serve embeddings."]
    CAP -->|yes| RES

    subgraph RES["resolve_routing() — lowest to highest precedence"]
        direction TB
        G["[routing] globals: default_strategy, cloud.fallback"]
        --> POLM["first matching routing.policies entry<br/>(model_prefix, api_format, source)"]
        --> SRCD["source defaults: strategy, local_only, allow_cloud,<br/>cloud_providers, prefer_provider"]
        --> SCR["source.scenarios[scenario] rule"]
        --> HDR["headers: x-netllm-strategy, x-netllm-backend"]
        --> LO["x-netllm-local-only / hops ≥ 2 — absolute ceiling"]
    end

    RES --> PIN{"x-netllm-backend pin<br/>resolvable & allowed?"}
    PIN -->|yes, attempt 1| USE([use pinned backend])
    PIN -->|no| CAND["backends_for_model()"]

    CAND --> F1["drop disabled · drop remote if !allow_remote<br/>· drop remote if local_only"]
    F1 --> F2["ModelResolver.serves(): one walk over<br/>model_aliases then model_pools groups"]
    F2 --> F3{"candidates<br/>empty?"}
    F3 -->|yes| REPROBE["force-refresh local probes, retry once"]
    REPROBE --> F4
    F3 -->|no| F4["prefer healthy; else all candidates"]
    F4 --> F5["exclude_ids (already-failed this request)"]
    F5 --> F6["prefer_provider narrowing"]
    F6 --> F7["cloud_provider_allowlist narrowing (cloud rows only)"]
    F7 --> F8["prefer_cloud (cloud.fallback = 'local')"]
    F8 --> F9["back-pressure: keep only backends under<br/>max_concurrency ?? max_in_flight_per_backend"]
    F9 --> STRAT{strategy}

    STRAT -->|auto| AUTO["batch_shard if shard ctx, else least_load"]
    STRAT -->|failover| FO["first untried in local→remote order"]
    STRAT -->|round_robin| RR["rotating index"]
    STRAT -->|least_load| LL["min in_flight; rotate among ties"]
    STRAT -->|latency_weighted| LW["min latency_ema_ms"]
    STRAT -->|local_first| LF["first local; shard_key indexes if &gt;1"]
    STRAT -->|local_spillover| LS["local while in_flight &lt; threshold,<br/>else strictly-less-loaded peer"]
    STRAT -->|batch_shard| BS["ledger assign, or HRW/modulo shard_index"]
```

### Strategy semantics cheat sheet

| Strategy | Balances by | Peer usage | Typical use |
|----------|-------------|------------|-------------|
| `local_first` | nothing | only if no local backend | single machine (default) |
| `local_spillover` | local in-flight threshold | only when local ≥ threshold **and** peer strictly less loaded | LAN default (auto-applied once on LAN bind) |
| `least_load` | live in-flight, ties rotated | full | busy mixed fleet |
| `latency_weighted` | EMA latency | full | heterogeneous hardware |
| `round_robin` | call count | full | even, model-identical fleet |
| `failover` | preference order | on failure | deterministic primary/secondary |
| `batch_shard` | shard key (HRW or modulo) | full | connector/batch workloads |
| `auto` | shard ctx → `batch_shard`, else `least_load` | full | recommended general default |

`batch_shard` without shard context degrades to `round_robin` and increments
`shardless_fallbacks` (surfaced in `/netllm/v1/status` and telemetry) — a deliberate
observability fix for a real field incident.

## Failover semantics

- **Retry budget** = `CandidateSchedule.max_attempts`, an explicit sum over the strategy
  phase *and* every fallback tier *and* the request-scoped cloud rows. Every failed backend
  id is added to `tried` and excluded from subsequent selection, so no attempt is ever burnt
  twice. Dialect ineligibility is carried separately as `schedule.ineligible_ids` — the old
  code overloaded `tried` for both jobs with three different initialisations.
- **The cap now binds on every surface.** It used to be `max(len(pool.backends), 1)`, which
  counted pool rows only: `/v1/messages` could exceed its own budget through the unbounded
  Anthropic fallback tier, and no surface counted the injected legacy cloud row. Operator-
  visible consequence: see [refactor/RELEASE-NOTES.md](refactor/RELEASE-NOTES.md).
- **Capacity vs hard failure.** `is_capacity_error()` classifies 409/429/503/507 and known
  body markers (`prefill_memory_exceeded`, `memory pressure`, `is busy`, `rate limit`) as
  *full now, not broken*: the backend is excluded for this request only and is **not** counted
  toward the offline trip. This prevents a loaded-but-working machine from being blackholed
  for `offline_retry_s` while its work piles onto survivors.
- **Load-aware strategies keep balancing on retries.** `least_load`, `latency_weighted`,
  `round_robin`, `local_spillover` are not downgraded to `failover` on attempt 2+.
- **Streaming is not retried after first byte.** Once any chunk has reached the client,
  a failure emits an SSE error frame and ends the stream rather than replaying a second
  response into the same stream. Correct, and non-obvious.

## Loop prevention across the mesh

```mermaid
sequenceDiagram
    participant C as Client
    participant A as Agent A
    participant B as Agent B (peer)
    participant P as B's local provider

    C->>A: POST /v1/chat/completions
    A->>A: selects peer:B (least_load)
    A->>B: POST /v1/chat/completions<br/>x-netllm-local-only: 1<br/>x-netllm-hops: 1
    B->>B: _wants_local_only() → true (header)
    Note over B: even if header were stripped,<br/>hops ≥ MAX_FORWARD_HOPS (2) forces local
    B->>P: upstream call
    P-->>B: response
    B-->>A: response
    A-->>C: response
```

Two independent guards (header + hop counter) mean a peer that ignores or strips
`x-netllm-local-only` still cannot ping-pong the request.

## Anthropic Messages path

`MessagesAdapter` runs the **same** engine loop as every other surface. "Anthropic last" is
expressed as data — `CandidateSchedule.fallback_tiers` — not as a second loop:

```mermaid
flowchart LR
    M["POST /v1/messages"] --> SCHED["MessagesAdapter.candidates()"]
    SCHED --> INEL["api_format='anthropic' rows →<br/>schedule.ineligible_ids"]
    SCHED --> TIER["same rows, ordered →<br/>schedule.fallback_tiers"]
    INEL --> LOOP["engine strategy phase:<br/>local + peer OpenAI-format backends"]
    LOOP -->|"selected"| TR["anthropic_to_openai_request()<br/>→ chat_completion()<br/>→ openai_to_anthropic_response()"]
    LOOP -->|"exhausted"| FBP["engine fallback phase<br/>(no strategy, no health filter)"]
    TIER --> FBP
    FBP --> NATIVE["AnthropicUpstream.messages_create()<br/>(x-api-key or Bearer per auth_mode)"]
```

This ordering is deliberate: the Anthropic cloud must never shadow the free local mesh in a
rotation. Streaming uses the identical schedule — the bespoke `candidates_exhausted` /
`fallback_iter` state machine that used to exist only inside `proxy_messages_stream` is
deleted, which is why the streamed and non-streamed Messages paths can no longer drift.

The two `cloud last` mechanisms are also one now. OpenAI-dialect surfaces put the
request-scoped cloud row in `extra_candidates` (a first-class strategy candidate, ordered by
`prefer_cloud` when `cloud.fallback = "local"`); Messages puts its Anthropic rows in
`fallback_tiers`. Both are fields of the same `CandidateSchedule`, walked by the same engine.

`anthropic_bridge` translates system prompts, multi-block content, tool definitions,
`tool_choice`, tool results, and — for streaming — synthesises `message_start`,
`content_block_start/delta/stop`, `message_delta`, `message_stop` events including
`tool_use` blocks. It imports no vendor SDK.

## Concurrency accounting

Three independent counters gate a request:

| Counter | Scope | Enforced in | Over-limit behaviour |
|---------|-------|-------------|----------------------|
| `Backend.in_flight` vs `max_concurrency` ?? `routing.max_in_flight_per_backend` | per backend row | `pool.select_backend` | prefer another backend; if all saturated, proceed anyway |
| `agent.max_concurrency` | per machine, self-declared | broadcast via heartbeat → copied onto the peer's `Backend.max_concurrency` on every other agent | peers stop selecting it |
| `sources[].max_concurrency` | per calling harness | `_source_admit` in `build_request_plan` (before the first `await`) | **HTTP 429**, no queuing — a real status even on `stream=true` |

Peer in-flight is heartbeat-reported plus this agent's own un-acked forwards
(`RouterPool._own_peer_hops`), so load is visible between heartbeats.

## Where the event loop is at risk

Selection is offloaded to a worker thread whenever a probe could fire
(`_offload_if_probing` → `pool.any_health_stale()`). The inversion that used to defeat that
guard — `_update_health_metrics()` issuing synchronous `httpx` calls on the event loop from
`refresh_local_backends()` and from every attempt's `finally` — was fixed by `3b6ec71`
(F-03): metrics read the health cache and never probe, and the doctor route is offloaded.

## Where model names are resolved

One walk, `netllm_core.model_resolution.ModelResolver`: alias exact → alias tag-prefix →
alias casefold → group exact → group tag-prefix → group casefold → catalog passthrough.
Candidacy (`resolver.serves`, used by `backends_for_model`), the invoked upstream name
(`resolver.upstream_model`) and the 404 hint (`resolver.known_models`) are three answers
*derived from that one walk*. There used to be two independent matchers with different arms,
which meant a backend could be selected because its catalog tag-prefix-matched a pool model
and then be invoked with a name it does not serve. The invariant "a selected backend is
always invoked with a name it advertises" is now asserted directly
(`tests/test_model_resolution_property.py`).
