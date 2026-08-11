# Telemetry API

Unified observability for the web dashboard and macOS menubar.

> **This document is normative** (F-49). The key tables below are the contract
> between the router and every telemetry client (`static/dashboard.js`,
> `ServingStatsMenuBuilder.swift`, `TelemetryPoller.swift`). The server always
> emits every documented key; a client must **read** them, never re-derive,
> sum, or fall back to an alternate key. `tests/contract/test_telemetry_contract.py`
> asserts the emitted key set is exactly the documented one, in both
> directions — adding a key to the payload without adding a row here fails
> CI, and so does documenting a key the server does not emit.

## Endpoint

`GET /netllm/v1/telemetry`

| Query | Default | Description |
|-------|---------|-------------|
| `scopes` | `router,omlx` | Comma-separated blocks: `router`, `omlx` |
| `history` | `60` | Include ring-buffer history (set `0` to omit) |
| `watch` | `true` | When true, refresh oMLX admin probes for this request |

Same host access as `/netllm/v1/status` (no admin token required on loopback).

## Response

```json
{
  "schema_version": 1,
  "ts": "2026-07-23T…",
  "router": {
    "session": { "requests": 0, "prompt_tokens": 0, "avg_prefill_tps": 0.0, … },
    "alltime": { … },
    "routed_requests": {},
    "capacity_rejections": {},
    "in_flight_total": 0,
    "backends": []
  },
  "omlx": { "available": false },
  "host": {
    "cpu_percent": 12.5,
    "memory_used_gb": 18.42,
    "memory_total_gb": 32.0,
    "memory_percent": 57.6
  },
  "history": {
    "router_rps": [],
    "omlx_pp_tps": [],
    "omlx_tg_tps": []
  },
  "subscribers": false
}
```

When an oMLX backend is online, `omlx.available` is true and `session` / `alltime` / `live` mirror oMLX Admin `/admin/api/stats` and `/admin/api/activity`.

Router all-time counters persist to `~/.config/netllm/stats.json`.

### Normative key set

Top level — always present: `schema_version`, `ts`, `host`, `subscribers`.
`router` is present iff `router ∈ scopes`; `omlx` iff `omlx ∈ scopes`;
`history` iff `history != 0`.

`subscribers` (bool, `app.py:256`) reports whether any client currently holds a
`watch=1` subscription; it is what gates the lazy oMLX admin probe. It was
emitted but undocumented until the Phase 10 contract test caught it.

**`router`** (`telemetry.py:_router_block` + `build_payload`)

| Key | Type | Notes |
|-----|------|-------|
| `session` | object | scope block, table below — counters since process start |
| `alltime` | object | scope block, table below — persisted counters |
| `routed_requests` | object | backend id → count |
| `capacity_rejections` | object | backend id → count |
| `shardless_fallbacks` | int | requests that fell back off a shard assignment |
| `in_flight_total` | int | sum over enabled backends |
| `windows` | object | windowed, dimensioned request ledger — table below (UI-1) |
| `latency` | object | rolling TTFT percentiles — table below (UI-2) |
| `live` | object | rolling throughput — table below (UI-2) |
| `backends` | array | per row: `id`, `provider`, `base_url`, `health`, `in_flight`, `p50_ms`, `p95_ms`, `samples`, `window_s` |

`backends[].p50_ms` / `p95_ms` are that backend's request latency over the last
`window_s` seconds, interpolated from a fixed log-spaced histogram, and are
**`null` when `samples` is 0** — a backend that has been health-probed but
never routed to has no latency to report, and `0.0` would be a lie. Per-backend
histograms are capped at 64 backends; past that a row reports `null`/`0`
rather than mixing several backends into one percentile.

**`router.windows`** (`telemetry.RequestLedger.windows_payload`) — the windowed
counters. Every other request counter in the agent is cumulative since process
start; these are not.

| Key | Type | Notes |
|-----|------|-------|
| `counters_since` | float | epoch seconds; when this ledger started counting |
| `spans_s` | array | **server-declared** span widths in seconds, e.g. `[60, 300, 86400]` |
| `by_backend` | object | backend id → `{"<span>": count}` |
| `by_model` | object | requested model → `{"<span>": count}` |
| `by_policy` | object | `"<index>:<name>"` of the matched routing policy → `{"<span>": count}`; `{}` means no policy ever matched |
| `by_source` | object | source id → `{requests, surfaces, top_models, last_seen_at}` |
| `truncated` | object | per dimension, requests folded into `__other__` because the key cap was hit |

Clients read the span keys present under a dimension; they never assume
`spans_s` and never sum buckets themselves (same rule as `total_tokens`).

Each `by_source` row: `requests` (`{"<span>": count}`), `surfaces`
(API dialect → cumulative count), `top_models` (array of `{model, count}`,
at most 5), `last_seen_at` (epoch seconds of that source's last completed
request — this, not a PATH check, is what a per-client live dot reads).

Cardinality is attacker-controlled — `by_model` is keyed on the client's
requested model string, and a LAN peer's models are republished through
`/v1/models` — so every dimension is capped at 256 keys. Keys past the cap are
accounted to a single `__other__` row and counted in `truncated`; a non-zero
`truncated` value means any top-N list built from that dimension is partial.

**`router.latency`** (`telemetry.RequestLedger.latency_payload`)

| Key | Type | Notes |
|-----|------|-------|
| `ttft_p50_ms` | float \| null | `null` when `ttft_samples` is 0 |
| `ttft_p95_ms` | float \| null | `null` when `ttft_samples` is 0 |
| `ttft_samples` | int | population size of the percentile |
| `window_s` | int | rolling window the percentiles describe |

Time-to-first-token is measured on the streaming path only: the wall-clock
gap between the attempt starting and the first SSE frame carrying generated
content. Non-streaming responses have **no observable TTFT** and are excluded
from the population rather than folded in as total latency — a mixed
population makes the percentile describe nothing. A deployment that only ever
issues non-streaming requests therefore reports `ttft_samples: 0` and two
`null`s, and the UI must render `—`.

**`router.live`** (`telemetry.RequestLedger.live_payload`)

| Key | Type | Notes |
|-----|------|-------|
| `prefill_tps` | float \| null | prompt tokens ÷ measured TTFT over the window; `null` when nothing streamed |
| `generation_tps` | float \| null | completion tokens ÷ measured generation time; `null` when nothing streamed |
| `requests_per_s` | float | completed requests ÷ `window_s` |
| `window_s` | int | rolling window, seconds |

**`router.session` / `router.alltime` scope block** (`_RouterCounters.to_dict`)

| Key | Type | Notes |
|-----|------|-------|
| `requests` | int | |
| `prompt_tokens` | int | |
| `completion_tokens` | int | |
| `total_tokens` | int | **server-computed**; clients must not sum |
| `avg_prefill_tps` | float \| null | 2dp; `null` when no request has contributed a measured prefill duration |
| `avg_generation_tps` | float \| null | 2dp; `null` when no request has contributed a measured generation duration |
| `uptime_s` | float | 1dp |

`avg_prefill_tps` and `avg_generation_tps` are `null`, not `0.0`, until a
streaming request has been served. They used to be
`prompt_tokens / (0.3 × total_latency)` and
`completion_tokens / (0.7 × total_latency)` — two invented constants presented
as measurements (UI-2). Both denominators are now real measured seconds, so
the figures change for existing users: that is a correction, and clients must
render `null` as `—` rather than coercing it to zero.

**`omlx`** — `{"available": false}` when no oMLX backend is reachable, and
`{"available": false, "admin_url": …}` when one is configured but every probe
failed. When available: `available`, `admin_url`, `session`, `alltime`, `live`,
`primary_model`, `loaded_models`, `model_memory_used`. `session`/`alltime` may
be `null` if that individual probe failed.

**`omlx.session` / `omlx.alltime` scope block**
(`netllm_discovery.local._normalize_omlx_stats_scope` — the router normalizes
the oMLX admin payload, so this is a router contract, not an oMLX one)

| Key | Type | Notes |
|-----|------|-------|
| `total_prompt_tokens` | int | |
| `total_completion_tokens` | int | |
| `total_tokens` | int | **server-computed**; clients must not sum |
| `total_cached_tokens` | int | |
| `cache_efficiency_pct` | float | 2dp; derived server-side when oMLX omits it |
| `total_requests` | int | |
| `avg_prefill_tps` | float | 2dp |
| `avg_generation_tps` | float | 2dp |

**`omlx.live`** — `prefill_tps`, `generation_tps`, and (when the activity probe
succeeded) `active_requests`, `waiting_requests`. The default when the probe
fails is `{"prefill_tps": 0.0, "generation_tps": 0.0}`.

**`host`** — `cpu_percent`, `memory_used_gb`, `memory_total_gb`,
`memory_percent`; the whole block is `null` only if the `psutil` import fails.

**`history`** — `router_rps`, `router_tps`, `omlx_pp_tps`, `omlx_tg_tps`, each
a list of at most 60 floats.

`router_rps` (requests per second) and `router_tps` (total tokens per second)
are read straight off the ledger's second-resolution ring, oldest first, one
entry per real second. They used to be appended only from inside
`record_usage`, so an idle router never sampled: the sparkline held its last
values instead of decaying to zero, and the 60 entries spanned an unknown
wall-clock duration. An idle second now reads `0.0`.

The `host` block (CPU %, memory used/total/percent) is populated on all platforms — `psutil` is a hard dependency of netllm-agent; it is `null` only if the `psutil` import fails. Richer host metrics (E/P CPU split, memory breakdown) remain macOS menubar-only (native, not from this API).

## UI surfaces

| Surface | Path | What it shows |
|---------|------|----------------|
| Web dashboard **Serving** tab | `/ui/` → Serving | Router session/all-time (requests, tokens, TPS), `routed_requests`, `capacity_rejections`, backend health/in-flight, `source_requests` from status, oMLX live/session when available, `history.router_rps` sparkline |
| Web **Status** tab | `/ui/` → Status | High-level routing stats, routed requests (subset), in-flight backends |
| macOS **Serving Stats** submenu | Menubar → Serving Stats | Active/loaded model, router session/all-time, per-backend routed counts, oMLX rows when admin reachable |
| macOS **System Stats** | Menubar → System Stats | Native CPU/GPU/memory (not from telemetry API) |

Menubar telemetry polls `GET /netllm/v1/telemetry?watch=1&history=60` only while the menu is open. Swift clients must build URLs with a query-safe helper (`AgentHTTP.url` in netllm-mac); `URL.appendingPathComponent` breaks `?watch=1` paths.

Per-model request counts are intentionally not shown on the Models tab (no per-model counters in the router yet).
