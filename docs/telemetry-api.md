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
| `backends` | array | per row: `id`, `provider`, `base_url`, `health`, `in_flight` |

**`router.session` / `router.alltime` scope block** (`_RouterCounters.to_dict`)

| Key | Type | Notes |
|-----|------|-------|
| `requests` | int | |
| `prompt_tokens` | int | |
| `completion_tokens` | int | |
| `total_tokens` | int | **server-computed**; clients must not sum |
| `avg_prefill_tps` | float | 2dp |
| `avg_generation_tps` | float | 2dp |
| `uptime_s` | float | 1dp |

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

**`history`** — `router_rps`, `omlx_pp_tps`, `omlx_tg_tps`, each a list of at
most 60 floats.

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
