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

Host metrics (E/P CPU, memory breakdown) are macOS menubar-only today; `host` stays null in the agent response.
