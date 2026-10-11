# GrapheneOS Seedvault → Pi WebDAV → TNAS (night)

## On the Pi (already deployed)

- WebDAV: `http://192.168.50.138:8087/`
- Username: `seedvault`
- Password: see `~/homelab/.env` → `SEEDVAULT_PASSWORD`
- Files land in `~/homelab/data/seedvault/`
- **Was:** night drain rsync to TNAS `backups/pixel9/` — **timer disabled 2026-08-24**; WebDAV landing still works; TNAS copy is manual until a replacement exists

## On Pixel 9 (GrapheneOS)

1. Same Wi‑Fi as the Pi (`192.168.50.0/24`).
2. Settings → System → **Backup** (Seedvault).
3. Choose storage → **WebDAV** (or Nextcloud-compatible if listed).
4. URL: `http://192.168.50.138:8087/`
5. Username `seedvault` + password from `.env`.
6. Run a manual backup once; confirm new files under:

```bash
ls -la ~/homelab/data/seedvault/
```

7. TNAS overnight copy is currently off — after a manual rsync (or future Hub job), check TNAS `backups/pixel9/`.

## Notes

- Prefer HTTP on LAN only (no internet expose).
- If Seedvault requires HTTPS, put Caddy with a LAN cert in front later.
- Do not point Seedvault directly at the TNAS while disks are spun down.

## Overnight TNAS automation (gap)

As of 2026-08-24, `homelab-night-drain.timer` is disabled. Seedvault → TNAS overnight is **not** running until a Hub-based replacement is designed. WebDAV landing path remains available for manual/phone uploads.
