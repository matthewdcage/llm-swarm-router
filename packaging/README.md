# netllm release packaging

Build scripts for **macOS** (venvstacks + Swift app), **Linux** (deb/rpm), and **Windows** (portable zip). All platform artifacts are attached to one [GitHub Release](https://github.com/matthewdcage/llm-swarm-router/releases) by [.github/workflows/release.yml](../.github/workflows/release.yml).

## macOS (venvstacks + menubar app)

Produces the venvstacks Python layers that the Swift macOS bundle embeds.
Building the user-facing `.app` is owned by
[`apps/netllm-mac/Scripts/build.sh`](../apps/netllm-mac/Scripts/build.sh);
this directory only hands it a `_export/` tree of Python layers.

### Requirements

- macOS 15.0+ (Sequoia) recommended
- Apple Silicon (arm64)
- Python 3.11+ on the host
- `uv sync` from repo root (creates `uv.lock`)
- venvstacks: `uv pip install venvstacks` or `pip install venvstacks`

### Build

```bash
# Re-export venvstacks layers (lock + build + export; cold ~1-3 min)
uv run python packaging/build.py --venvstacks-only

# Fingerprint for build.sh cache decisions
uv run python packaging/build.py --print-fingerprint
```

Then the Swift bundle:

```bash
apps/netllm-mac/Scripts/build.sh release
apps/netllm-mac/Scripts/build.sh release --no-rebuild-donor   # reuse _export/
```

Output: `dist/netllm-mac.dmg` (release workflow renames to `llm-swarm-router.dmg`).

### macOS layout

```
packaging/
├── _build/         # venvstacks intermediate layers (gitignored)
├── _export/        # venvstacks export, embedded into the .app
└── requirements-netllm.txt  # generated at build time
```

| Layer | Contents |
|-------|----------|
| Runtime (`cpython-3.11`) | Python 3.11.10 |
| Framework (`framework-netllm`) | FastAPI, uvicorn, zeroconf, SDKs, workspace deps |

No application layer, the Swift menubar app is the application surface.
Workspace packages (`netllm_*`) are rsync'd as pure source into the bundle.

## Linux (deb / rpm)

Scripts: [`packaging/linux/`](linux/)

```bash
NETLLM_VERSION=0.0.0-dev ./packaging/linux/build-deb.sh
NETLLM_VERSION=0.0.0-dev ./packaging/linux/build-rpm.sh
# → dist/*.deb, dist/*.rpm
```

Rehearse install: `./scripts/emulate-user-install-linux.sh`

## Windows (portable zip + service)

Scripts: [`packaging/windows/`](windows/)

```powershell
.\packaging\windows\build-zip.ps1 -Version 0.0.0-dev
# → dist/netllm-0.0.0-dev-windows-x64.zip
```

Winget manifest template: [`packaging/windows/winget/netllm.yaml`](windows/winget/netllm.yaml) (SHA256 updated in release job).

Rehearse install (Admin): `.\scripts\emulate-user-install-windows.ps1`

## CI smoke

`./scripts/ci.sh packaging` builds deb+rpm on Ubuntu and zip on Windows (same checks as the `packaging-smoke` job in CI).
