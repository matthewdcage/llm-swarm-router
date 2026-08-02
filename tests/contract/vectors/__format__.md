# ContractVector format (`tests/contract/vectors/*.json`)

One JSON document per scenario × path, replayed by
`tests/contract/test_vectors.py` against the REAL `app.py` routes with a
real `AgentService` wired to FakeFarm backends (`tests/contract/farm.py`).

```json
{
  "id": "chat-ns-happy",
  "divergence": [],
  "scenario": {
    "backends": [
      {
        "name": "alpha",
        "api_format": "openai",
        "models": ["farm-chat"],
        "script": [{"behavior": "ok"}],
        "local": true
      }
    ],
    "routing": {"default_strategy": "failover"},
    "cloud": {}
  },
  "request": {
    "path": "chat_ns",
    "body": {"model": "farm-chat", "messages": [...]},
    "headers": {}
  },
  "expected": {
    "status": 200,
    "body_shape": {...},
    "sse_frames": [["data: {...}"], ...],
    "upstream_calls": [
      {"backend": "alpha", "method": "POST", "path": "/v1/chat/completions",
       "model": "farm-chat", "headers": {"authorization": "Bearer farm-key"}}
    ],
    "pool_delta": {...},
    "metrics_delta": {"counters": {...}, "gauges": {...}},
    "admission_delta": {...}
  }
}
```

## Fields

- **id** — unique slug; keep it equal to the filename stem.
- **divergence** — list of behavior-matrix IDs (`D1`–`D15`) this vector
  embodies. The lint (`test_divergence_lint.py`) requires any vector that
  differs from `git HEAD` to carry IDs declared in
  `tests/contract/allowed-divergences.txt` (empty by default = no vector
  may change).
- **scenario** — the world to build:
  - `backends[]` — FakeFarm backends. `script` is a per-request behavior
    list (last entry repeats); behaviors: `ok`, `ok_stream(chunks,
    usage_in_final)`, `http(status)` for 409/429/500/502/503/507,
    `capacity_body_marker(marker, status)`, `midstream_drop(after_n)`,
    `bad_json`. See `farm.py` docstring.
  - `routing` / `cloud` / `agent` / `swarm` — dict-merged over
    `NetllmConfig()` defaults (e.g. `model_aliases`, `sources`,
    `default_strategy`).
- **request** — `path` is one of `chat_ns`, `chat_s`, `emb`,
  `messages_ns`, `messages_s`, `responses_ns`, `responses_s` (`_s` paths
  get `stream: true` injected); `body`/`headers` go on the wire verbatim.
- **expected** — recorded observable outcome, canonicalized by
  `canonical.py` (volatile-field schema: ids/timestamps normalized,
  latency sums → `">0"`, latency EMA → `"updated"`):
  - `status` — HTTP status from the route layer.
  - `body_shape` — canonicalized JSON body (null for streams).
  - `sse_frames` — canonicalized frame/line lists (null for non-streams).
  - `upstream_calls` — ordered inference calls the farm received:
    backend, method, path, per-backend `model`, and the routing-relevant
    header subset (peer loop-guard headers, auth, `x-netllm-*`). Health
    probes are logged separately (`farm.probes`) and are NOT part of the
    vector.
  - `pool_delta` — routed_counts/capacity_rejections deltas, final
    in_flight (leak detector: must be 0), health status, EMA movement.
  - `metrics_delta` — Prometheus counter deltas + final gauge values
    scoped to this scenario's backends.
  - `admission_delta` — per-source and per-(source, scenario) count
    deltas, final `_source_in_flight` (must be 0), `_request_count`
    delta, shardless fallbacks.

## Recording

```sh
NETLLM_VECTOR_RECORD=1 uv run pytest tests/contract/test_vectors.py
```

rewrites every vector's `expected` block in place (sorted keys, 2-space
indent). Recording twice must produce identical bytes — nondeterminism is
a harness bug. New vector files start without an `expected` block and are
filled in by the first recording run.

## Determinism rules baked into the harness

- FakeFarm emits fixed ids/timestamps and no real sleeps.
- Scripted error responses carry `x-should-retry: false` so the official
  SDKs never add internal retries/backoff — one router attempt equals one
  recorded upstream call.
- Legacy cloud env keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `CLAUDE_CODE_OAUTH_TOKEN`) are scrubbed for the duration of a run so
  vectors never depend on the recording machine's environment.
