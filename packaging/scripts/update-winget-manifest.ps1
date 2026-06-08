param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [Parameter(Mandatory = $true)]
    [string]$ZipPath,
    [string]$Repo = "matthewdcage/llm-swarm-router",
    [string]$ManifestPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $ManifestPath) {
    $Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $ManifestPath = Join-Path $Root "packaging\windows\winget\netllm.yaml"
}

if (-not (Test-Path $ZipPath)) {
    Write-Error "Zip not found: $ZipPath"
}

$Hash = (Get-FileHash -Path $ZipPath -Algorithm SHA256).Hash
$Tag = if ($Version -match '^v') { $Version } else { "v$Version" }
$Ver = $Tag.TrimStart('v')
$Url = "https://github.com/$Repo/releases/download/$Tag/netllm-$Ver-windows-x64.zip"

$content = @"
# yaml-language-server: `$schema=https://aka.ms/winget-manifest.singleton.1.6.0.schema.json
PackageIdentifier: matthewdcage.netllm
PackageVersion: $Ver
PackageLocale: en-US
Publisher: matthewdcage
PackageName: netllm
License: MIT
ShortDescription: Mesh router for local LLM backends with OpenAI and Anthropic APIs
Moniker: netllm
Installers:
  - Architecture: x64
    InstallerType: zip
    InstallerUrl: $Url
    InstallerSha256: $Hash
ManifestType: singleton
ManifestVersion: 1.6.0
"@

Set-Content -Path $ManifestPath -Value $content -Encoding utf8NoBOM
Write-Host "Updated $ManifestPath (SHA256: $Hash)"
