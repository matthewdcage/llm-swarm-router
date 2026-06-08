# Vendor SDK versions

Single source of truth for **OpenAI** and **Anthropic** Python SDK pins used by netllm adapter packages.

Vendor SDKs live only in `packages/netllm-sdk-openai` and `packages/netllm-sdk-anthropic`. `netllm-core` must not import `openai` or `anthropic` (enforced by `tests/test_sdk_isolation.py`).

## Current pins

| SDK | Package | Floor pin (`pyproject.toml`) | Resolved (`uv.lock`) | Last validated |
|-----|---------|------------------------------|----------------------|----------------|
| OpenAI | `netllm-sdk-openai` | `openai>=1.60` | 2.41.0 | 2026-06-08 |
| Anthropic | `netllm-sdk-anthropic` | `anthropic>=0.45` | 0.106.0 | 2026-06-08 |

Update the **Resolved** and **Last validated** columns whenever you bump a floor pin or commit an updated `uv.lock`.

## Upstream changelogs

| SDK | Changelog / releases |
|-----|---------------------|
| OpenAI Python SDK | https://github.com/openai/openai-python/releases |
| Anthropic Python SDK | https://github.com/anthropics/anthropic-sdk-python/releases |

## Change layers

When an upstream release adds or changes API behavior, classify the work:

| Layer | Path | Examples |
|-------|------|----------|
| 1 — Adapter | `packages/netllm-sdk-*/src/*/client.py` | SDK constructor, `create`/`stream` calls, `model_dump()`, SSE wire format |
| 2 — Bridge | `packages/netllm-core/src/netllm_core/anthropic_bridge.py` | Messages ↔ Chat Completions mapping, tools, stream events |
| 3 — Agent | `packages/netllm-agent/src/netllm_agent/service.py` | Routing, failover, headers, backend selection |
| Probes | `packages/netllm-core/src/netllm_core/health.py` | Discovery compat signals |

## Bump checklist

One SDK package per PR.

1. Edit `openai>=…` or `anthropic>=…` in the matching `packages/netllm-sdk-*/pyproject.toml`
2. `uv sync` (updates `uv.lock` — commit both files)
3. Update this table (resolved version + date)
4. Read the upstream changelog; change only the layer(s) from the table above
5. Run targeted tests:

```bash
./scripts/ci.sh sdk
./scripts/ci.sh
```

6. Open PR with the SDK bump section in the PR template filled out

## Automation

| Mechanism | Purpose |
|-----------|---------|
| `uv.lock` in git | Reproducible CI and packaging (`uv sync --frozen`) |
| Dependabot (`sdk-bump` label) | Weekly PRs for `packages/netllm-sdk-*/pyproject.toml` |
| `./scripts/ci.sh sdk` | Adapter + bridge + isolation contract tests |
| `.github/workflows/sdk-canary.yml` | Weekly install of latest SDKs; opens `sdk-canary` issue on failure |

Manual smoke after adapter changes (requires running agent + backend):

```bash
./netllm test
./netllm test --api anthropic
```

## Where users see SDK versions

| Surface | Location |
|---------|----------|
| Web dashboard | **Status → System** — OpenAI SDK / Anthropic SDK rows (from `GET /netllm/v1/version`) |
| macOS Settings | **System** card — Agent version, OpenAI SDK, Anthropic SDK |
| API | `GET /netllm/v1/version` → `sdk_versions.openai`, `sdk_versions.anthropic` |
