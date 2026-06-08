# netllm macOS App Packaging

Produces the venvstacks Python layers that the Swift macOS bundle embeds.
Building the user-facing `.app` is owned by
[`apps/netllm-mac/Scripts/build.sh`](../apps/netllm-mac/Scripts/build.sh);
this directory only hands it a `_export/` tree of Python layers.

## Requirements

- macOS 15.0+ (Sequoia) recommended
- Apple Silicon (arm64)
- Python 3.11+ on the host
- `uv sync` from repo root (creates `uv.lock`)
- venvstacks: `uv pip install venvstacks` or `pip install venvstacks`

## Build

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

## Output

```
packaging/
├── _build/         # venvstacks intermediate layers (gitignored)
├── _export/        # venvstacks export — embedded into the .app
└── requirements-netllm.txt  # generated at build time
```

## Layer Configuration

| Layer | Contents |
|-------|----------|
| Runtime (`cpython-3.11`) | Python 3.11.10 |
| Framework (`framework-netllm`) | FastAPI, uvicorn, zeroconf, SDKs, workspace deps |

No application layer — the Swift menubar app is the application surface.
Workspace packages (`netllm_*`) are rsync'd as pure source into the bundle.
