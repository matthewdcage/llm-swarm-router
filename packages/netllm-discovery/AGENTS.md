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
