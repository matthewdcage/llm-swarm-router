# netllm-core

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Shared routing, backend health cache, configuration I/O, model catalog types, and Anthropic Messages bridge logic. No FastAPI, no vendor SDK imports.

## Ownership

Key modules: `config.py`, `routing_policy.py`, `pool.py`, `health.py`, `models.py`, `anthropic_bridge.py`, `batch.py`, `update.py`.

## Local Contracts

- Config path: `~/.config/netllm/config.toml` (see root `config.example.toml`)
- `anthropic_bridge.py` translates Messages API ↔ OpenAI-compatible backends; SDK calls stay in `netllm-sdk-anthropic`
- Routing strategies and backend health drive agent and CLI behavior

## Work Guidance

- Keep pydantic models in `models.py`; avoid circular imports with agent/discovery
- Platform helpers in `platform.py` and `install_detect.py` are shared with CLI

## Verification

```bash
./scripts/ci.sh test   # tests/ cover routing and bridge
```

## Child DOX Index

None — flat `src/netllm_core/` package.
