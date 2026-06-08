# netllm-mac

Native Swift menubar app that supervises the netllm Python agent (port 11400).

## Build

```bash
# From repo root
uv sync
uv pip install venvstacks
apps/netllm-mac/Scripts/build.sh release
open apps/netllm-mac/build/Stage/netllm-mac.app
```

## Structure

| Path | Role |
|------|------|
| `Sources/App/` | App entry, delegate, lifecycle |
| `Sources/Menubar/` | Status item, stats polling |
| `Sources/Server/` | Process supervisor, control socket |
| `Sources/Config/` | TOML slices, CLI shim |
| `Sources/Welcome/` | First-run wizard |
| `Sources/Updater/` | GitHub Releases checker |

See [docs/menubar-app.md](../../docs/menubar-app.md) for user-facing documentation.
