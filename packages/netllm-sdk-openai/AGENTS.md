# netllm-sdk-openai

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Isolated OpenAI Python SDK adapter. Only package that imports `openai`; upstream version pinned in `pyproject.toml` and [../../docs/sdk-versions.md](../../docs/sdk-versions.md).

## Ownership

Key module: `client.py`. Contract tests: `tests/test_openai_upstream_contract.py`.

## Local Contracts

- One SDK bump per PR: edit dep → `uv sync` → update sdk-versions doc → `./scripts/ci.sh sdk`
- Adapter changes follow upstream changelog; see sdk-versions.md change layers

## Work Guidance

- Keep surface minimal — expose what `netllm-core` and `netllm-agent` need
- Do not leak OpenAI types into `netllm-core`

## Verification

```bash
./scripts/ci.sh sdk
./scripts/ci.sh test
```

## Child DOX Index

None.
