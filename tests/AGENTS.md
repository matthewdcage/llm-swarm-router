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
- **Routing hardening:** `tests/test_routing_hardening.py` — per-request strategy/pin headers, hop-count loop backstop, peer-row pruning, offline re-probe window, config hot-apply, one-shot LAN defaults, merge-safe `config import`; `tests/test_agent.py::test_messages_api_round_robin_reaches_peer` locks the Messages-path strategy fix
- **Swarm acceptance harness:** `tests/test_e2e_two_agents.py` runs two real agents + mock providers over HTTP (combined catalog, load spreading, loop-guarded hops, scan TTL). Extend it for any mesh behavior change; contract invariants live in `tests/test_contract.py`
- **Open LAN swarm CLI/doctor:** `tests/test_cli_swarm_init.py` (open vs `--secure` init, `swarm-token --create`); `tests/test_doctor_open_lan.py` (no token issue on LAN); `tests/test_config_json.py` (`import_config` applies `ensure_lan_mesh_defaults`)
- macOS install scripts: `tests/test_bundled_install_scripts.sh`
- Menubar agent start (quiet + LAN listen): `tests/test_serve_quiet_lan.py` — regression for bundled `serve -q` with `0.0.0.0` listen reaching uvicorn
- Admin access: `tests/test_agent.py` — remote client 403; same-host LAN IP allowed via `local_admin_client_hosts`

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
