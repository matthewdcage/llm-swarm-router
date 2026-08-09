# Stub — a local inference provider

Guide: [../02-local-provider.md](../02-local-provider.md) ·
Kit: `tests/conformance/kit_local.py`

## 1. Registry entry

`packages/netllm-core/src/netllm_core/local_providers.py`, inside
`LOCAL_PROVIDERS`:

```python
    "myserver": LocalProviderSpec(
        id="myserver",
        display_name="My Server",          # discovery result rows
        short_label="MyServer",            # CLI tables; keep it narrow
        default_ports=(9000,),             # scanned on 127.0.0.1 AND localhost
        platforms=("darwin", "linux", "win32"),
        port_env="MYSERVER_PORT",          # "" if the server has none
        api_key_env="MYSERVER_API_KEY",    # "" if it needs no key
        # default_api_key="myserver-local",  # only if the server ships one
        # host_env="MYSERVER_HOST",          # HOST-style var, Ollama-shaped
        # default_host_port=9000,            # required with host_env
        offline_hint="run [cyan]myserver serve[/]",
    ),
```

A field earns its place only when **≥2 entries** set it non-default
([PROGRAM.md](../PROGRAM.md) §7). One entrant's quirk is a hook.

## 2. Companion 1 — `ProviderId` (runtime-enforced)

`packages/netllm-core/src/netllm_core/models.py`:

```python
ProviderId = Literal[
    "omlx", "ollama", "lmstudio", "vllm", "myserver",
    "custom", "anthropic", "openai",
]
```

Skip it and `Backend(provider="myserver")` raises `ValidationError`.

## 3. Companion 2 — macOS offline prefill (projection-enforced)

`apps/netllm-mac/Sources/AppView/SettingsViewModel.swift`, inside
`localProviderBootstrap`:

```swift
        (id: "myserver", label: "MyServer", port: 9000),
```

`label` must equal `short_label`; `port` must be one the registry really
scans.

## 4. Regenerate

```bash
python3 scripts/generate-registry-artifacts.py
```

## 5. Verify

```bash
uv run pytest tests/conformance/kit_local.py -k myserver
uv run pytest tests/extending -q
./scripts/ci.sh lint
```
