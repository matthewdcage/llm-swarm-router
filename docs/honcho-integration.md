# Honcho integration guide

Use **netllm** as a drop-in OpenAI-compatible router for Honcho instead of embedding multi-endpoint URLs in `config.toml` or connector env vars.

## Prerequisites

- netllm agent running on the host: `netllm serve` (default `http://127.0.0.1:11400/v1`)
- Local inference (oMLX, Ollama, or LM Studio) reachable from the agent

## Honcho core (deriver, dialectic, dream)

Once the router is stable, simplify Honcho `routing_chain` to a single row pointing at netllm:

```toml
[deriver.model_config]
transport = "openai"
model = "gemma-4-26b-a4b-it-8bit"
routing_strategy = "failover"

[deriver.model_config.overrides]
base_url = "http://127.0.0.1:11400/v1"
api_key = "netllm-local"
```

Or via Docker (agent on host, Honcho in containers):

```toml
base_url = "http://host.docker.internal:11400/v1"
```

Rebuild api/deriver after config change: `docker compose build api deriver && docker compose up -d --force-recreate api deriver`

## Connectors

Replace comma-separated `LLM_OPENAI_COMPATIBLE_BASE_URLS` with one URL:

```bash
LLM_OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11400/v1
CONNECTOR_LLM_ROUTING_STRATEGY=batch_shard
```

Configure netllm for connector batch sharding:

```toml
[routing]
default_strategy = "batch_shard"
```

Each parallel connector worker should send shard headers (or an OpenAI `user` field) so requests stick to the correct backend:

```http
X-Netllm-Batch-Id: enrichment-run-abc123
X-Netllm-Shard-Index: 17
```

Or without custom headers:

```json
{"user": "netllm:enrichment-run-abc123:17", "model": "...", "messages": [...]}
```

On failure, the agent reassigns that index to the next healthy backend (Honcho-style failed-index retry). Without shard context, `batch_shard` falls back to round-robin with a warning in agent logs.

## Swarm (multi-Mac)

1. Run `netllm serve` on each Mac with local oMLX/Ollama
2. Enable mDNS in config (`swarm.mdns = true`) or add static peers:

```toml
[swarm]
peers = ["http://192.168.1.50:11400", "http://192.168.1.51:11400"]
mdns = true
```

3. Optionally set one machine as gateway: `netllm gateway`
4. Point Honcho at the gateway URL only

## Parity checklist

Before removing Honcho's embedded `endpoint_pool`:

| Feature | Honcho embedded | netllm |
|---------|-----------------|--------|
| Multi-endpoint failover | Yes | Yes |
| Batch sharding | Yes (connectors) | Yes (`batch_shard` + shard headers) |
| Per-endpoint API keys | Yes | Yes (`routing.backends`) |
| Health cache + circuit breaker | Yes | Yes |
| LAN discovery | No | Yes |
| Drop-in OpenAI base URL | No | Yes |
| Streaming proxy | Limited | Yes |

## Benchmark

Compare connector enrichment throughput:

```bash
# Start agent first: netllm serve
./scripts/benchmark-parity.sh http://127.0.0.1:11400 20 your-model-name
```

Run the same workload against Honcho's embedded pool (`LLM_OPENAI_COMPATIBLE_BASE_URLS`) and compare elapsed time plus `[llm-pool]` log lines vs netllm `/metrics` counters (`netllm_requests_total`, `netllm_request_latency_seconds`).

## Migration policy

Keep Honcho embedded routing until:

1. All parity checklist items verified on your network
2. Streaming chat works through netllm for dialectic
3. Connector batch runs show equal or better throughput

Do **not** delete Honcho `src/llm/endpoint_pool.py` until the above pass, netllm is a parallel consumer path first.
