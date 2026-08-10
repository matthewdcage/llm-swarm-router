# netllm-cli

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Typer CLI entry point for init (guided single/swarm), join, swarm-token, serve, lifecycle (start/stop/restart), doctor, models, peers, gateway, cloud provider management, and config editing. Repo root `./netllm` wraps `uv run`.

## Ownership

Key modules: `main.py` (Typer wiring only, ~80 lines), `commands/` (one module per command
group: `_common`, `init_install`, `join_swarm`, `observe`, `serve_lifecycle`, `diagnose`,
`config_io`, `cloud`, `sources`, `connect`), `ui.py`, `install.py`, `install_detect.py`,
`config_json.py`, `oauth_pkce.py`. Platform lifecycle: `lifecycle/darwin.py`, `linux.py`,
`windows.py`, `common.py`.

## Local Contracts

- Prefer `./netllm` from repo root in docs; global `netllm` after `./netllm install`
- `doctor` must pass before declaring setup complete
- **Guided swarm:** `init` prompts single-vs-swarm only on a TTY (non-TTY stays single-machine — contract-tested); `init --swarm` upgrades existing configs to open LAN mesh (`local_spillover`, `subnet_scan`, no token); `init --swarm --secure` mints `cluster_token`; `swarm-token --create` / `--rotate` for secured pairing; `join` validates token via 401-aware heartbeat and rejects self-joins / open-swarm token mismatch
- Background agent: macOS menubar/Homebrew, Linux systemd user unit, Windows service (see platform docs)
- **`serve -q` warnings:** use `print_warnings()` from `ui.py` only — never `console.print(..., file=...)` (Rich 13+ rejects `file=`; menubar supervises with `-q`, so startup warnings must not crash before uvicorn)
- **`netllm cloud` sub-app:** `list`/`enable`/`disable`/`set-key`/`verify`/`fallback`/`test`/`connect` edit `config.toml`'s `[cloud]` section directly (no running agent required) — mirrors `config_app`'s pattern of reading `load_config`/writing `save_config`, never the admin HTTP API. `cloud enable --auth` validates against the provider's `CloudProviderSpec.auth_modes`. `cloud connect openrouter` is the only OAuth path (PKCE, `oauth_pkce.py`) — everything else is `set-key`. `enable`, `set-key` and `connect` all run the live credential check (`netllm_core.cloud_verification.probe_cloud_provider`) and write its outcome to `[cloud.providers.<id>].verified_*`: `enable` refuses when the check proves the key cannot work, because `config_guards.enforce_cloud_provider_verification` would refuse the same config from any other writer
- **`oauth_pkce.py`** is intentionally CLI-only, not netllm-core: it needs `webbrowser`, `http.server`, and a real network round-trip to openrouter.ai, none of which belong in the shared routing package. The local callback server (`start_local_callback_server`/`wait_for_callback`) is a one-shot `http.server.HTTPServer.handle_request()` on a daemon thread — tests exercise it with a real loopback HTTP request rather than mocking the socket layer
- **`netllm connect <id>`**: prints per-harness wiring (env exports, Codex TOML snippet); `--json`, `--print-env`, optional `--toggle` to register/enable `routing.sources` — never edits editor configs or shell profiles
- **`netllm drain [on|off]`**: hits the *running* agent's `POST /netllm/v1/admin/drain` (httpx, like `status`/`test` — not a config edit, no `save_config`). Runtime-only on the agent side; the CLI has nothing to persist

## Extension contract

- **Owns:** the **commands** — the Typer app in `main.py` (wiring only) and
  the command modules under `commands/`. A `ControlDescriptor`'s `cli` tuple
  names leaf command paths as Typer renders them, and
  `tests/conformance/kit_config_surfaces.py::test_action_controls_have_a_real_cli_command`
  resolves them by real introspection, not by grep.
- **Consumes only** for provider and harness facts. `ui.py`'s
  `_PROVIDER_LABELS` and `default_provider_port_hint()` are derived from
  `LOCAL_PROVIDERS`; the four-arm `elif` chains they replaced were three of
  Axis B's eleven parallel maps.
- **No new mirrors:** never add a provider, surface or harness id literal
  here. A per-provider string belongs on the spec (`short_label`,
  `offline_hint`), not in an `elif`.
- **Known thin spot — a second harness roster.** `commands/connect.py`'s
  `_guides()` is a hand-written dict keyed by the same ids as
  `KNOWN_HARNESSES`. A registry-only harness addition passes validation and
  then raises `KeyError` in the primary onboarding command;
  `tests/test_known_harnesses.py::test_every_known_harness_has_a_connect_guide`
  is the parity assert that stops it. `HarnessSpec` (which deletes `_guides`)
  is Phase F1 and has **not landed**.
- **Adding a command or control:** [docs/extending/04-cli-and-control-plane.md](../../docs/extending/04-cli-and-control-plane.md).
  Adding a harness: [docs/extending/06-harness-integration.md](../../docs/extending/06-harness-integration.md).

## Work Guidance

- Match Typer/Rich patterns already in `commands/`; `main.py` is wiring only
- Lifecycle changes must align with [../../packaging/AGENTS.md](../../packaging/AGENTS.md) install artifacts

## Verification

```bash
./netllm doctor
./netllm status
./scripts/ci.sh test
```

## Child DOX Index

None — lifecycle subfolder is part of this package.
