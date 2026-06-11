# Honcho integration guide

Use **netllm** as a drop-in OpenAI-compatible router for Honcho instead of embedding multi-endpoint URLs in `config.toml` or connector env vars.

Honcho follows the same client rules as every other tool: [editor-integration.md](editor-integration.md#client-configuration-all-tools). Summary for Honcho:

1. Set deriver/dialectic `base_url` and connector `LLM_OPENAI_COMPATIBLE_BASE_URL` to netllm once (`http://127.0.0.1:11400/v1`, or `http://host.docker.internal:11400/v1` from Docker).
2. Keep your existing **model names**; netllm matches them against `./netllm models`.
3. Configure oMLX, Ollama, LM Studio, vLLM, and swarm peers in **`~/.config/netllm/config.toml`**, not in Honcho. Remove Honcho's embedded endpoint pool when migration is verified (see [Migration policy](#migration-policy)).
4. With swarm peers visible (`./netllm peers`), routing across machines is automatic; Honcho does not need per-machine backend URLs.

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

1. Run `netllm serve --host 0.0.0.0` on each Mac with local oMLX/Ollama
2. Enable mDNS in config (`swarm.mdns = true`) or add static peers:

```toml
[swarm]
peers = ["http://192.168.1.50:11400", "http://192.168.1.51:11400"]
mdns = true

[routing]
default_strategy = "round_robin"
allow_remote = true
```

3. Optionally set one machine as gateway: `netllm gateway`
4. Point Honcho at the **gateway agent URL only** (`http://<gateway>:11400/v1`)

### Agent-hop routing

Honcho sends one request to the gateway. The gateway picks a backend:

- **Local:** `http://127.0.0.1:8080/v1` (oMLX/Ollama on the gateway host)
- **Peer:** `http://<peer-LAN-IP>:11400/v1` (the peer's netllm agent; that agent forwards to its own loopback oMLX)

Do not point Honcho at peer loopback URLs (`127.0.0.1:8080` on another machine). The mesh uses **agent hops**, not exported loopback inference URLs.

Verify distribution after traffic:

```bash
curl -s http://127.0.0.1:11400/metrics | rg netllm_requests_total
curl -s http://<peer-LAN-IP>:11400/metrics | rg netllm_requests_total
```

Both counters should increase when `round_robin` alternates across machines with the same model.

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
