# Linux/Windows beta QA log

Validation for v0.2.2 beta packaging and install paths.

## Automated (CI)

| Check | Status | Notes |
|-------|--------|-------|
| pytest ubuntu-latest | Pass | After omlx provider + install_detect fixes |
| pytest windows-latest | Pass | Same |
| packaging-smoke Linux | CI | deb + rpm via `scripts/ci.sh packaging` |
| packaging-smoke Windows | CI | zip + Scripts/netllm.exe verification |
| unified release.yml | CI | Parallel macOS + Linux + Windows on publish |

## Linux packaging (Docker ubuntu:24.04)

| Step | Status | Notes |
|------|--------|-------|
| `uv sync --no-dev` into `/usr/lib/netllm` | Pass | Replaces deprecated `uv pip install --no-dev` |
| `build-deb.sh` | Pass | `dist/netllm_0.2.2-ci_amd64.deb` produced |
| `build-rpm.sh` | CI (x86_64) | RPM `BuildArch: x86_64`; use GitHub Actions ubuntu-latest (not aarch64 Docker) |
| systemd user install (`emulate-user-install-linux.sh`) | Manual | Requires real Linux host with user systemd session |

## Windows packaging

| Step | Status | Notes |
|------|--------|-------|
| `build-zip.ps1` | CI | venv layout at zip root: `Scripts/netllm.exe` |
| `install-service.ps1` | Manual | Requires Admin PowerShell on Windows host |
| `emulate-user-install-windows.ps1` | Manual | Full rehearsal on Windows VM |

## Manual checklist (host required)

Run on bare metal or VM — not macOS.

**Linux:** [`scripts/emulate-user-install-linux.sh`](../scripts/emulate-user-install-linux.sh)

**Windows:** [`scripts/emulate-user-install-windows.ps1`](../scripts/emulate-user-install-windows.ps1) (Admin)

After install: `scripts/agent-verify-setup.sh`, http://127.0.0.1:11400/ui/

## Release assets (v0.2.2 beta)

Expected on GitHub Release:

- `llm-swarm-router.dmg` (macOS stable)
- `netllm_0.2.2_amd64.deb`
- `netllm-0.2.2-*.rpm`
- `netllm-0.2.2-windows-x64.zip`
- `packaging/windows/winget/netllm.yaml` (SHA256 filled by release job)
