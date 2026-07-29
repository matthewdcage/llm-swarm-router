# Telemetry API

Unified observability for the web dashboard and macOS menubar.

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
  "host": null,
  "history": {
    "router_rps": [],
    "omlx_pp_tps": [],
    "omlx_tg_tps": []
  }
}
```

When an oMLX backend is online, `omlx.available` is true and `session` / `alltime` / `live` mirror oMLX Admin `/admin/api/stats` and `/admin/api/activity`.

Router all-time counters persist to `~/.config/netllm/stats.json`.

Host metrics (E/P CPU, memory breakdown) are macOS menubar-only today; `host` stays null in the agent response unless `psutil` is installed on the agent host (Linux).

## UI surfaces

| Surface | Path | What it shows |
|---------|------|----------------|
| Web dashboard **Serving** tab | `/ui/` → Serving | Router session/all-time (requests, tokens, TPS), `routed_requests`, `capacity_rejections`, backend health/in-flight, `source_requests` from status, oMLX live/session when available, `history.router_rps` sparkline |
| Web **Status** tab | `/ui/` → Status | High-level routing stats, routed requests (subset), in-flight backends |
| macOS **Serving Stats** submenu | Menubar → Serving Stats | Active/loaded model, router session/all-time, per-backend routed counts, oMLX rows when admin reachable |
| macOS **System Stats** | Menubar → System Stats | Native CPU/GPU/memory (not from telemetry API) |

Menubar telemetry polls `GET /netllm/v1/telemetry?watch=1&history=60` only while the menu is open. Swift clients must build URLs with a query-safe helper (`AgentHTTP.url` in netllm-mac); `URL.appendingPathComponent` breaks `?watch=1` paths.

Per-model request counts are intentionally not shown on the Models tab (no per-model counters in the router yet).
