# netllm-sdk-anthropic

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Isolated Anthropic Python SDK adapter. Only package that imports `anthropic`; upstream version pinned in `pyproject.toml` and [../../docs/sdk-versions.md](../../docs/sdk-versions.md).

## Ownership

Key module: `client.py`. Contract tests: `tests/test_client_contract.py`.

## Local Contracts

- One SDK bump per PR: edit dep → `uv sync` → update sdk-versions doc → `./scripts/ci.sh sdk`
- Bridge translation lives in `netllm-core/anthropic_bridge.py`, not here

## Work Guidance

- Keep surface minimal — expose what bridge and agent need for Messages API
- Do not leak Anthropic types into `netllm-core`

## Verification

```bash
./scripts/ci.sh sdk
./netllm test --api anthropic
```

## Child DOX Index

None.
