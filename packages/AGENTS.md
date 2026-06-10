# packages — Python uv workspace

## Purpose

Source of truth for netllm Python code. Six workspace members under `src/` layout; version-locked via root `pyproject.toml` and `uv.lock`.

## Ownership

| Package | Role |
|---------|------|
| `netllm-core` | Routing, health cache, config, Anthropic bridge |
| `netllm-discovery` | Local provider scan, swarm registry, mDNS |
| `netllm-agent` | FastAPI daemon: `/v1/*`, `/netllm/v1/*`, `/metrics`, `/ui/` |
| `netllm-cli` | Typer CLI (`./netllm` wrapper) |
| `netllm-sdk-openai` | OpenAI SDK upstream adapter (isolated vendor dep) |
| `netllm-sdk-anthropic` | Anthropic SDK upstream adapter (isolated vendor dep) |

Parent rail: [../AGENTS.md](../AGENTS.md).

## Local Contracts

- `netllm-core` must never import `openai` or `anthropic`; vendor SDKs live only in `netllm-sdk-*`
- Workspace deps use `[tool.uv.sources]` with `workspace = true`
- Bump one SDK package per PR; follow [../docs/sdk-versions.md](../docs/sdk-versions.md)
- Python 3.11+, ruff line length 88, basedpyright `standard`

## Work Guidance

- Match existing module layout: `packages/<name>/src/<name>/`
- CLI patterns follow `netllm-cli` (Typer + Rich)
- Agent HTTP surface changes need tests under `tests/` and possibly `netllm-agent` static dashboard updates

## Verification

```bash
./scripts/ci.sh lint
./scripts/ci.sh test
./scripts/ci.sh sdk   # after SDK bumps
```

## Child DOX Index

| Path | Contract |
|------|----------|
| [`netllm-core/AGENTS.md`](netllm-core/AGENTS.md) | Routing, config, health, bridge |
| [`netllm-discovery/AGENTS.md`](netllm-discovery/AGENTS.md) | Discovery and swarm |
| [`netllm-agent/AGENTS.md`](netllm-agent/AGENTS.md) | FastAPI agent and dashboard |
| [`netllm-cli/AGENTS.md`](netllm-cli/AGENTS.md) | Typer CLI and lifecycle |
| [`netllm-sdk-openai/AGENTS.md`](netllm-sdk-openai/AGENTS.md) | OpenAI SDK adapter |
| [`netllm-sdk-anthropic/AGENTS.md`](netllm-sdk-anthropic/AGENTS.md) | Anthropic SDK adapter |
