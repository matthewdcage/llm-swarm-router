param(
    [string]$Version = "0.2.1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Stage = Join-Path $Root "packaging\windows\stage"
$Dist = Join-Path $Root "dist"
$Prefix = Join-Path $Stage "netllm"

Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

Push-Location $Root
try {
    uv sync
    uv pip install -e . --no-dev --target $Prefix
} finally {
    Pop-Location
}

Copy-Item (Join-Path $PSScriptRoot "install-service.ps1") (Join-Path $Prefix "install-service.ps1")

@'
@echo off
"%~dp0python\Scripts\netllm.exe" %*
'@ | Set-Content -Encoding ASCII (Join-Path $Prefix "netllm.cmd")

$ZipName = "netllm-$Version-windows-x64.zip"
Compress-Archive -Path (Join-Path $Prefix "*") -DestinationPath (Join-Path $Dist $ZipName) -Force
Write-Host "Built $(Join-Path $Dist $ZipName)"
