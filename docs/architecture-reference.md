# Reference architecture extracted from Honcho LLM routing (2026-06-06)

Honcho source paths used as reference (not imported):

- `src/llm/endpoint_pool.py` — failover, batch_shard, circuit breaker
- `src/llm/local_health.py` — GET /v1/models probe
- `src/routers/local_providers.py` — provider scan, diagnose
- `connectors/_shared/llm.py` — batch workers, failed-index retry
- `connector-runner/src/llm_pool.py` — per-URL API keys

Ported into `netllm-core` and `netllm-discovery` as standalone packages.

Run full graphify on Honcho paths for interactive exploration:

```bash
cd /path/to/Honcho
/graphify src/llm connectors/_shared/llm.py connector-runner/src/llm_pool.py src/routers/local_providers.py
```
