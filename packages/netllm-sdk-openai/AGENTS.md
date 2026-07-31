# netllm-sdk-openai

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Isolated OpenAI Python SDK adapter. Only package that imports `openai`; upstream version pinned in `pyproject.toml` and [../../docs/sdk-versions.md](../../docs/sdk-versions.md).

## Ownership

Key modules: `client.py` (SDK adapter), `payload.py` (wire-payload
normalization). Contract tests: `tests/test_openai_upstream_contract.py`,
`tests/test_payload_adaptation.py`, `tests/test_sdk_param_drift.py`.

## Local Contracts

- One SDK bump per PR: edit dep → `uv sync` → update sdk-versions doc → `./scripts/ci.sh sdk`
- Adapter changes follow upstream changelog; see sdk-versions.md change layers
- **Payload contract** (`payload.py`): every wire payload is normalized before
  the SDK call — client `extra_body` is flattened to top level, SDK control
  kwargs (`extra_headers`, `extra_query`, `timeout`) are stripped entirely
  (never forwarded), and field aliases are mapped (`repeat_penalty` →
  `repetition_penalty` for OpenAI-format backends). Fields typed on the pinned
  SDK method pass as-is; everything else is routed via `extra_body`, which the
  SDK merges into the top-level HTTP JSON (so vLLM/Ollama/LM Studio see e.g.
  `top_k`, `min_p` where they expect them). The typed-param sets are
  drift-checked against the pinned SDK in `tests/test_sdk_param_drift.py`.

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
