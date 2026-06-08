# Build and install like an end user: zip → Windows service.
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $Version) {
    $Version = & uv run --directory $Root python -c "import tomllib; print(tomllib.load(open(r'$Root/pyproject.toml','rb'))['project']['version'])"
}

Write-Host "==> Windows install rehearsal for netllm"
Write-Host "    Run PowerShell as Administrator for service registration."
Write-Host

& "$Root/packaging/windows/build-zip.ps1" -Version $Version

$Zip = Get-ChildItem "$Root/dist/netllm-*-windows-x64.zip" | Select-Object -First 1
$InstallDir = Join-Path $env:LOCALAPPDATA "netllm-rehearsal"
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Expand-Archive -Path $Zip.FullName -DestinationPath $InstallDir -Force

Write-Host "==> Registering service from $InstallDir"
Push-Location $InstallDir
try {
    & ".\install-service.ps1"
} finally {
    Pop-Location
}

Write-Host "==> Starting agent"
& "$InstallDir\netllm.cmd" start

Write-Host @"

Done. Verify:
  - Browser: http://127.0.0.1:11400/ui/
  - Terminal: netllm status (if on PATH)
  - Logs: %LOCALAPPDATA%\netllm\logs\agent.log

Install dir: $InstallDir

"@
