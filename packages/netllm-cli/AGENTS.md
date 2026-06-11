# netllm-cli

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Typer CLI entry point for init (guided single/swarm), join, swarm-token, serve, lifecycle (start/stop/restart), doctor, models, peers, gateway, and config editing. Repo root `./netllm` wraps `uv run`.

## Ownership

Key modules: `main.py`, `ui.py`, `install.py`, `install_detect.py`, `config_json.py`. Platform lifecycle: `lifecycle/darwin.py`, `linux.py`, `windows.py`, `common.py`.

## Local Contracts

- Prefer `./netllm` from repo root in docs; global `netllm` after `./netllm install`
- `doctor` must pass before declaring setup complete
- **Guided swarm:** `init` prompts single-vs-swarm only on a TTY (non-TTY stays single-machine — contract-tested); `init --swarm` upgrades existing configs to open LAN mesh (`local_spillover`, `subnet_scan`, no token); `init --swarm --secure` mints `cluster_token`; `swarm-token --create` / `--rotate` for secured pairing; `join` validates token via 401-aware heartbeat and rejects self-joins / open-swarm token mismatch
- Background agent: macOS menubar/Homebrew, Linux systemd user unit, Windows service (see platform docs)
- **`serve -q` warnings:** use `print_warnings()` from `ui.py` only — never `console.print(..., file=...)` (Rich 13+ rejects `file=`; menubar supervises with `-q`, so startup warnings must not crash before uvicorn)

## Work Guidance

- Match Typer/Rich patterns already in `main.py`
- Lifecycle changes must align with [../../packaging/AGENTS.md](../../packaging/AGENTS.md) install artifacts

## Verification

```bash
./netllm doctor
./netllm status
./scripts/ci.sh test
```

## Child DOX Index

None — lifecycle subfolder is part of this package.
