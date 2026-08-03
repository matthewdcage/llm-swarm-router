# Windows install: netllm

Run the mesh router in a terminal with `netllm serve`.

> **Known issue (F-34): the `NetllmAgent` Windows service does not start.**
> `install-service.ps1` registers `netllm.exe serve` directly with `sc.exe`, but
> a Win32 service must complete a Service Control Manager handshake
> (`StartServiceCtrlDispatcher`) within ~30 seconds of launch, and netllm does
> not implement one. Both `netllm start` and `sc start NetllmAgent` therefore
> fail with **error 1053**. The service is also registered without an `obj=`
> account, so it would run as LocalSystem — more privilege than the agent needs.
>
> **Use `netllm serve` instead** (Task Scheduler → "At log on" if you want it
> automatic). The rest of the Windows package — PATH setup, CLI, dashboard,
> mesh — is unaffected. Tracked as F-34 in
> [architecture/09-follow-up-audit-2026-07-31.md](architecture/09-follow-up-audit-2026-07-31.md).

**Troubleshooting:** [windows-troubleshooting.md](windows-troubleshooting.md) · **All platforms:** [platform-matrix.md](platform-matrix.md)

## Portable zip (recommended)

Download `netllm-<version>-windows-x64.zip` from [GitHub Releases](https://github.com/matthewdcage/llm-swarm-router/releases/latest) (e.g. `netllm-0.2.3.5-windows-x64.zip` from [v0.2.3.5](https://github.com/matthewdcage/llm-swarm-router/releases/tag/v0.2.3.5), **alpha** package for Windows).

1. Extract to a folder (e.g. `%LOCALAPPDATA%\netllm`).
2. Open PowerShell **as Administrator** in that folder.
3. Run `.\install-service.ps1` (sets up PATH; the service it registers cannot
   start — see the note above).
4. From any terminal: `netllm init` then `netllm serve`.

Logs: `%LOCALAPPDATA%\netllm\logs\agent.log` (and service stdout).

**Status:** `netllm status` · dashboard http://127.0.0.1:11400/ui/

`install-service.ps1` adds the package `Scripts\` folder (contains `netllm.exe`) to your user PATH. Open a **new** terminal after install so PATH updates apply.

## Winget

Each release attaches a **`netllm.yaml`** manifest snippet (SHA256 + download URL) for maintainers to merge into [winget-pkgs](https://github.com/microsoft/winget-pkgs). After that PR lands:

```powershell
winget install matthewdcage.netllm
```

Until winget-pkgs is updated, use the portable zip from Releases.

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

**Guided:** start a swarm or join one — both write LAN bind, cluster token, and load-spreading config for you:

```powershell
netllm init --swarm                          # first machine; prints the join command
netllm join http://<host>:11400 --token <t>  # every other machine
netllm start
netllm peers
```

**Manual** (existing config): `netllm serve --host 0.0.0.0`.

Windows firewalls often block mDNS. Allow it once (Admin PowerShell):

```powershell
netsh advfirewall firewall add rule name="netllm mDNS" dir=in protocol=UDP localport=5353 action=allow
netsh advfirewall firewall add rule name="netllm agent" dir=in protocol=TCP localport=11400 action=allow
```

`netllm doctor` prints these when discovery looks blocked. LAN-bound agents auto-run one subnet scan when mDNS finds no peers within 10s; `swarm.peers` in `config.toml` and `netllm peers --subnet-scan` still work.

## Wire editors

```powershell
netllm env   # print export lines (same as dashboard Copy client env)
$env:OPENAI_BASE_URL = "http://127.0.0.1:11400/v1"
$env:OPENAI_API_KEY = "netllm-local"
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:11400"
$env:ANTHROPIC_API_KEY = "netllm-local"
```

See [editor-integration.md](editor-integration.md), [platform-matrix.md](platform-matrix.md), and [windows-troubleshooting.md](windows-troubleshooting.md).
