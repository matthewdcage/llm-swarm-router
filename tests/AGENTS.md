# tests — cross-package integration tests

## Purpose

pytest suite exercising routing, agent HTTP surfaces, Anthropic bridge, CLI, discovery, and bundled install scripts across workspace packages.

## Ownership

- Root `tests/` — integration and contract tests
- Package-local tests: `packages/netllm-sdk-*/tests/` (SDK upstream contracts)

Parent rail: [../AGENTS.md](../AGENTS.md).

## Local Contracts

- Runner: pytest with asyncio mode auto (root config)
- Fixtures: `tests/fixtures/` (e.g. Anthropic message payloads)
- Add tests for real behavior; avoid trivial assertions
- macOS install scripts: `tests/test_bundled_install_scripts.sh`
- Menubar agent start (quiet + LAN listen): `tests/test_serve_quiet_lan.py` — regression for bundled `serve -q` with `0.0.0.0` listen reaching uvicorn

## Work Guidance

- Agent or routing changes should extend `tests/` before merge
- SDK bumps must pass `./scripts/ci.sh sdk` and contract tests in sdk packages
- Menubar e2e lives in `scripts/test-menubar-e2e.sh` (not pytest); includes bundled **quiet + 0.0.0.0 listen** smoke on Stage `.app` before DMG attach

## Verification

```bash
./scripts/ci.sh test
./scripts/ci.sh              # lint + test
scripts/verify-before-pr.sh
```

## Child DOX Index

| Path | Contract |
|------|----------|
| [`fixtures/`](fixtures/) | Shared test payloads |

Fixtures are data only; no AGENTS.md.
