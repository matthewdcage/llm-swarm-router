# netllm-discovery

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Local LLM provider discovery (oMLX, Ollama, LM Studio, vLLM), LAN peer registry, and optional mDNS swarm advertisement/browse.

## Ownership

Key modules: `local.py`, `swarm.py`, `mdns.py`, `lan.py`, `runtime.py`, `process_util.py`.

## Local Contracts

- Default probe ports: oMLX `:8080`, Ollama `:11434`, LM Studio `:1234`, vLLM `:8000`
- Custom ports via `[discovery].custom_endpoints` or `[[routing.backends]]` in config
- mDNS requires `zeroconf` from `uv sync`; LAN swarm needs agent `serve --host 0.0.0.0`
- Set `swarm.cluster_token` on untrusted networks
- **Agent-hop routing:** `SwarmRegistry.peer_agent_backends()` emits one `Backend` per peer at `{listen_url}/v1`; never merge peer loopback oMLX URLs into a gateway pool
- **No transitive echo:** `_peer_backend_models()` unions only a peer's `local=true` rows — peers advertise models they serve directly, never their own remote `peer:` rows
- `lan.filter_own_peer_urls()` strips this host's agent URL from `swarm.peers` on save/scan

## Work Guidance

- Discovery results feed `netllm-core` routing; keep scan logic side-effect free where possible
- Optional `mdns` extra on `netllm-agent` for zeroconf

## Verification

```bash
./netllm discover
./netllm peers      # with agent on 0.0.0.0
./scripts/ci.sh test
```

## Child DOX Index

None — flat `src/netllm_discovery/` package.
