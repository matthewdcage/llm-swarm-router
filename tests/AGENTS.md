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
- **Coordinator platform (local maintainer; gitignored):** `tests/fixtures/coordinator-snapshot/`, `tests/fixtures/coordinator-monitor/`, `tests/test_coordinator_dispatch.py`, `tests/test_coordinator_engagement_monitor.py` — offline harness for `.cursor/coordinator/` scripts; never on remote
- Add tests for real behavior; avoid trivial assertions
- **Routing hardening:** `tests/test_routing_hardening.py` — per-request strategy/pin headers, hop-count loop backstop, peer-row pruning, offline re-probe window, config hot-apply, one-shot LAN defaults, merge-safe `config import`; `tests/test_agent.py::test_messages_api_round_robin_reaches_peer` locks the Messages-path strategy fix
- **Swarm acceptance harness:** `tests/test_e2e_two_agents.py` runs two real agents + mock providers over HTTP (combined catalog, load spreading, loop-guarded hops, scan TTL). Extend it for any mesh behavior change; contract invariants live in `tests/test_contract.py`
- **Status polling / peer probes:** `tests/test_status_peer_probe.py` — `/netllm/v1/status` (including `probe=1`) must not HTTP-probe peer agent `/v1` surfaces
- **Open LAN swarm CLI/doctor:** `tests/test_cli_swarm_init.py` (open vs `--secure` init, `swarm-token --create`); `tests/test_doctor_open_lan.py` (no token issue on LAN); `tests/test_config_json.py` (`import_config` applies `ensure_lan_mesh_defaults`)
- **Conformance kits (`tests/conformance/`)**: registry-parameterized suites plus `projections.py` (read a fact back out of Swift/JS/TOML/Markdown *with its source location*) and `ledgers/*.toml` (every exception carries `reason` + `expires`, enforced). `kit_config_surfaces.py` owns Axis D — cross-surface control parity: every config schema key must be `schema_rendered`, `hand_rendered`, `derived` (read_only), or ledgered in `ledgers/control-parity.toml`. Day-one: 18 of 188 (field, surface) pairs ledgered = 9.6%, against a 20% tripwire that is itself a test. Add a config field and this kit fails by name until a control exists or an absence is dated. Never grep a surface for a field name directly — `source_region` strips comments first and `_names` requires a quoted key or a word-bounded property access, because a comment naming a control is not that control. See [docs/extending/08-control-parity.md](../docs/extending/08-control-parity.md)
- macOS install scripts: `tests/test_bundled_install_scripts.sh`
- Menubar agent start (quiet + LAN listen): `tests/test_serve_quiet_lan.py` — regression for bundled `serve -q` with `0.0.0.0` listen reaching uvicorn
- Admin access: `tests/test_agent.py` — remote client 403; same-host LAN IP allowed via `local_admin_client_hosts`
- **Request-aware model pools:** `tests/test_model_pools.py`, `tests/test_model_resolution_property.py` (D19: overflow deferred to pool two-phase collect); contract vector `naming-model-pools-isolation-multi-host` (B5 multi-host isolation)
- **Agent-hop routing:** `tests/test_agent_hop_routing.py` — `exact_model_only` on terminating peer, peer pin headers
- **Live mesh smoke (maintainer):** `scripts/live-routing-smoke.sh` — multi-node LAN validation (health, alias, pool isolation, peer pin, pressure); honors `NETLLM_CLUSTER_TOKEN` when secured; clears bundled-app `PYTHONHOME` for telemetry JSON parsing

## Work Guidance

- Agent or routing changes should extend `tests/` before merge
- SDK bumps must pass `./scripts/ci.sh sdk` and contract tests in sdk packages
- Dashboard telemetry UI contract: `tests/test_dashboard_telemetry.py` (Serving tab source + scenario counters + `routerScopeBlock` markers)
- **`netllm connect` CLI:** `tests/test_cli_connect.py` (env/json/toggle wiring; mocked health)
- **Contract lint renames:** `tests/contract/test_divergence_lint.py` — stable vector `id` → HEAD path (F-56)
- Menubar e2e: `scripts/test-menubar-e2e.sh` (bundled quiet + 0.0.0.0 listen on Stage `.app`); lifecycle: `scripts/test-menubar-lifecycle.sh` (L5b adopt + `settingsStatusLabel`); Settings/menubar strings: `NetllmMacTests.AgentSupervisorStatusLabelTests`, `MenubarStatusTitleTests`; manual adopt: [docs/solutions/menubar-adopt-smoke.md](../docs/solutions/menubar-adopt-smoke.md)

## Verification

```bash
./scripts/ci.sh test
./scripts/ci.sh              # lint + test
uv run pytest tests/contract -q   # 373 golden vectors
scripts/verify-before-pr.sh
```

## Child DOX Index

| Path | Contract |
|------|----------|
| [`fixtures/`](fixtures/) | Shared test payloads |

Fixtures are data only; no per-fixture AGENTS.md unless a fixture tree grows maintenance docs.

Updated: 2026-08-03 (Phase B closeout: 363 contract vectors incl. B5 pool isolation; connect CLI; Settings statusLabel tests; 1091 CI pytest)
