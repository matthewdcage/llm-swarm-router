# netllm Windows packaging

Build scripts produce a portable zip with venv + service install helper. MSI/winget
manifests reference GitHub Release artifacts from `release-windows.yml`.

## Layout

```
packaging/windows/
├── build-zip.ps1       # Portable install + NetllmAgent service registration
├── install-service.ps1 # Register Windows service (used by zip post-install)
└── winget/netllm.yaml  # Winget manifest template
```

## Portable zip

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-zip.ps1
```

Output: `dist/netllm-<version>-windows-x64.zip`

## Service

After unzip, run as Administrator:

```powershell
.\install-service.ps1
netllm start
```

Service name: `NetllmAgent` (matches CLI lifecycle dispatch).
