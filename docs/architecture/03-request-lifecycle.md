# 03 · Request lifecycle and routing

## The four proxy surfaces

| Route | Entry point | Translation |
|-------|-------------|-------------|
| `POST /v1/chat/completions` | `proxy_chat_completion[_stream]` | none (native) |
| `POST /v1/responses` | `proxy_responses[_stream]` | Responses ⇄ chat at the edge, then reuses the chat path verbatim |
| `POST /v1/embeddings` | `proxy_embeddings` | none; Anthropic-format backends pre-excluded |
| `POST /v1/messages` | `proxy_messages[_stream]` | Messages ⇄ chat per selected backend; Anthropic-native backends called directly |

The Responses surface exists because Codex CLI removed Chat Completions support for custom
providers (Feb 2026); netllm translates once at the edge so every downstream behaviour —
source identity, per-source routing, scenarios, capacity, failover — is shared.

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
    API->>S: proxy_chat_completion(payload, headers)
    S->>SI: resolve_source(headers, routing.sources)
    SI-->>S: ResolvedSource(id, resolved_via, authenticated)
    S->>SC: classify_scenario(payload, api_format, UA)
    SC-->>S: long_context | web_search | think | background | default
    S->>S: model_rewrites → scenario.model → capability guard
    S->>RP: resolve_routing(globals, policies, source, scenario, headers)
    RP-->>S: ResolvedRouting(strategy, local_only, allow_cloud_inject,<br/>prefer_provider, pinned_backend, cloud_leads, allowlist)
    S->>S: _check_source_capacity() → 429 if over cap
    S->>S: refresh_local_backends()  [10 s TTL scan + peer merge + prune]
    opt routing.allow_cloud_inject
        S->>S: inject env-key cloud backend + materialize [cloud.providers.*]
    end
    loop attempt ≤ len(pool.backends)
        S->>P: select_backend(model, strategy, exclude=tried, ...)
        P-->>S: Backend | None
        S->>P: acquire(backend) + _source_acquire()
        S->>U: chat_completion({**payload, model: _model_for_backend()})
        alt success
            U-->>S: response
            S->>P: mark_success(backend, latency)
            S->>S: metrics + telemetry + shard ledger
            S-->>C: response (model rewritten back to requested name)
        else OpenAIUpstreamError
            U-->>S: error
            S->>P: mark_failure(backend, capacity=is_capacity_error(...))
            S->>S: tried.add(backend.id) → next attempt
        end
        S->>P: release(backend) + _source_release()
    end
    S-->>C: last error, or 404 "model not found on any backend"
```

## Routing decision flow

```mermaid
flowchart TD
    START([request]) --> SRC["resolve_source()<br/>header → netllm-&lt;id&gt;[.secret] key → User-Agent → 'default'"]
    SRC --> SCEN["classify_scenario()<br/>long_context &gt; web_search &gt; think &gt; background &gt; default"]
    SCEN --> REW["model_rewrites → scenario.model"]
    REW --> CAP{"model_capability<br/>== chat?"}
    CAP -->|no| E400["400 — cannot serve chat"]
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
    F1 --> F2["match served catalog against model_aliases<br/>OR model_pools membership"]
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

- **Retry budget** = `max(len(pool.backends), 1)`; every failed backend id is added to
  `tried` and excluded from subsequent selection, so no attempt is ever burnt twice.
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

`proxy_messages` runs the **same** strategy loop as chat completions over the OpenAI-format
mesh, then falls back to Anthropic-format backends as a final tier:

```mermaid
flowchart LR
    M["POST /v1/messages"] --> EXCL["exclude every api_format='anthropic'<br/>backend from the strategy loop"]
    EXCL --> LOOP["strategy loop over local + peer<br/>OpenAI-format backends"]
    LOOP -->|"selected"| TR["anthropic_to_openai_request()<br/>→ chat_completion()<br/>→ openai_to_anthropic_response()"]
    LOOP -->|"exhausted"| FB["_anthropic_fallback_backends()"]
    FB --> NATIVE["AnthropicUpstream.messages_create()<br/>(x-api-key or Bearer per auth_mode)"]
```

This ordering is deliberate: the Anthropic cloud must never shadow the free local mesh in a
rotation. Streaming mirrors it with an explicit `candidates_exhausted` / `fallback_iter`
state machine.

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
| `sources[].max_concurrency` | per calling harness | `_check_source_capacity` | **HTTP 429**, no queuing |

Peer in-flight is heartbeat-reported plus this agent's own un-acked forwards
(`RouterPool._own_peer_hops`), so load is visible between heartbeats.

## Where the event loop is at risk

The code carefully offloads *selection* to a worker thread only when a probe could fire
(`_offload_if_probing` → `pool.any_health_stale()`). But `_update_health_metrics()` — called
from `refresh_local_backends()` on **every** request and again in the `finally` of every
attempt — iterates all backends calling `RouterPool.is_healthy()`, which performs
**synchronous httpx calls on the event loop** when an entry is stale. This inverts the
intent of the offload guard. See F-04.
