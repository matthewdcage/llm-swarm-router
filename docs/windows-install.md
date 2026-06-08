# Windows install — netllm

Run the mesh router as a **Windows service** or in a foreground terminal.

## Portable zip (recommended)

Download `netllm-<version>-windows-x64.zip` from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases).

1. Extract to a folder (e.g. `%LOCALAPPDATA%\netllm`).
2. Open PowerShell **as Administrator** in that folder.
3. Run `.\install-service.ps1` to register the `NetllmAgent` service.
4. From any terminal: `netllm init` then `netllm start`.

Logs default to `%LOCALAPPDATA%\netllm\logs`.

## Winget

After the release manifest is published:

```powershell
winget install matthewdcage.netllm
```

## Source install (development)

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```powershell
git clone https://github.com/matthewdcage/llm-swarm-router.git
cd llm-swarm-router
uv sync
.\netllm init
.\netllm serve
```

Default discovery probes **Ollama** (`:11434`), **LM Studio** (`:1234`), and **vLLM** (`:8000`).

## WSL vs native

- **Native Windows**: Ollama for Windows, LM Studio for Windows, vLLM in a CUDA-capable environment.
- **WSL2**: Run inference servers inside WSL; run `netllm serve` in the same WSL distro so localhost probes succeed.

## LAN swarm

```powershell
netllm serve --host 0.0.0.0
```

Windows firewalls often block mDNS. Prefer `swarm.peers` in `config.toml` or `netllm peers --subnet-scan` when browse returns no peers.

## Wire editors

```powershell
$env:OPENAI_BASE_URL = "http://127.0.0.1:11400/v1"
$env:OPENAI_API_KEY = "netllm-local"
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:11400"
$env:ANTHROPIC_API_KEY = "netllm-local"
```

See [editor-integration.md](editor-integration.md).
