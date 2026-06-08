param(
    [string]$InstallDir = $PSScriptRoot
)

$ErrorActionPreference = "Stop"
$ServiceName = "NetllmAgent"
$NetllmCmd = Join-Path $InstallDir "netllm.cmd"

if (-not (Test-Path $NetllmCmd)) {
    Write-Error "netllm.cmd not found in $InstallDir"
}

$BinPath = Join-Path $InstallDir "Scripts\netllm.exe"
$Existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($Existing) {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 2
}

$BinPathEscaped = $BinPath -replace '\\', '\\'
sc.exe create $ServiceName binPath= "`"$BinPath`" serve -q" start= auto DisplayName= "netllm Agent"

$BinDir = Join-Path $InstallDir "Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$BinDir*") {
    $NewPath = if ($UserPath) { "$UserPath;$BinDir" } else { $BinDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Host "Added $BinDir to user PATH (open a new terminal for netllm)"
}

Write-Host "Registered $ServiceName — run: netllm start"
