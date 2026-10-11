# Failures, limitations, and test matrix

Audience: audit agent. QC runbook: [`QC-SYSTEM-PROCESSES.md`](../QC-SYSTEM-PROCESSES.md). Job health: [`JOB-HEALTH-AND-AGENT-REVIEW.md`](../JOB-HEALTH-AND-AGENT-REVIEW.md).

## Operational incidents (documented 2026-10)

| Incident | Cause | Mitigation |
|----------|-------|------------|
| BB/Office complete packs failed | Size, dead swarms, HTTP 400 | Season-by-season; dismiss jobs; purge partials |
| Jellyfin missing BB S01 | Sonarr import path / API unreachable | `SONARR_URL=127.0.0.1:8989` in Gluetun; manual DownloadedEpisodesScan |
| Jobs visible after file delete | Job JSON persists until **dismiss** | `POST …/dismiss`, `dismiss-queue`; done jobs dismissable (2026-10) |
| hub-form crash on deploy | `app.py` imports `job_health` without file on Pi | Deploy `job_health.py` with `app.py` |
| TNAS WoL no response | NAS hard off or WoL disabled in TOS | Physical power; enable WoL in NAS UI |
| Pi ↔ worker HTTP 400 on PATCH | Token mismatch, payload, or load | Check `hub-worker.log`, `HUB_WORKER_TOKEN` |
| aria2 resume broken | Restart dropped gids | **Retry** not Resume |
| aria2 error 13 | Partials without `.aria2`, `allow-overwrite=false` | Purge + retry |
| USB D-state cascades | Stale `/mnt/external/*` mounts | `mount-watchdog.sh`, worker timeouts |
| MeTube / YouTube | Cookie expiry | Re-export cookies (`ytdlp-cookies-README.md`) |

## Known limitations (by design)

- Trusted home LAN; open mesh netllm separate from Hub auth model
- `machines.json` / `hub-worker.env` not in git
- TNAS optional for torrents; required for canonical Website Backups
- Leech-only — no seeding after complete
- Remote Hub queue is read-only unless cluster token (netllm pattern — Hub uses worker token for worker only)
- Glance iframe cache — manual `?v=` bump after JS deploy
- Coordinator/outreach not deployed

## Automated tests (2026-10-12 compile)

Command:

```bash
cd homelab/bucket-hub
PYTHONPATH=. uv run --with pytest pytest tests/ -q
```

| Result | Detail |
|--------|--------|
| **54 passed** | |
| **2 failed** | `test_movies_usb_root_inbox` (download_destinations); `test_pause_rejects_non_aria2` (pause API behaviour drift) |
| **Requirement** | `PYTHONPATH=.` (repo root uv venv) |

### Test modules

| File | Covers |
|------|--------|
| `test_hub_torrent_parse.py` | Season parse, release kind, pack queries |
| `test_media_pipeline.py` | Sonarr/Radarr payloads |
| `test_download_destinations.py` | aria2 paths, USB roots |
| `test_job_concurrency.py` | Claim slots, archive vs torrent |
| `test_job_pause.py` | Pause/cancel/purge |
| `test_job_queue_reorder.py` | Drag reorder API |
| `test_job_health.py` | Stuck metadata, agent_review |
| `test_aria2_bandwidth_slots.py` | Mbps gating |
| `test_torrent_indexer_*.py` | Jackett catalog URLs |

## Smoke / LAN scripts

| Script | Purpose |
|--------|---------|
| `scripts/hub-lan-smoke-test.py` | Pi Hub APIs from LAN |
| `scripts/hub-torrent-jellyfin-smoke.sh` | Torrent → import slice |
| `scripts/verify-jackett-catalog.sh` | Indexer health |
| `scripts/jellyfin-connectivity-review.sh` | Playback path |

**Compile note:** LAN smokes not re-run from maintainer Mac in this session (may be off-LAN). Auditor should run on LAN or via Tailscale to Pi.

## Manual scenarios (audit must verify)

1. **TV season** — Search `Show season 2` → queue → complete → Sonarr → Jellyfin episode visible
2. **Dismiss failed** — Error row leaves mini queue after dismiss
3. **Archive** — `example.com` depth 2 → staging or TNAS folder populated
4. **NAS offline** — Archive falls back to `_nas_staging` with UI warning
5. **aria2 restart** — Retry job succeeds after clearing stale gid
6. **VPN** — Egress test green; Jackett search returns rows
7. **WoL** — ubuntu wake from Machines toggle; TNAS documented as best-effort

## Deploy drift checklist (Pi vs git)

Checked **2026-10-12** (byte size match):

| File | Pi `~/homelab/hub-form/` | Git `homelab/bucket-hub/` |
|------|--------------------------|---------------------------|
| app.py | 138588 | 138588 |
| form.html | 232730 | 232730 |
| job_health.py | 9451 | 9451 |
| hub-worker.py | 122279 | (sync to `~/homelab/scripts/` separately) |

**Action:** confirm worker copy on ai-server after worker changes.

## Gaps in knowledge (needs live probe)

- Jackett healthy indexers count vs configured (8/30 UI snapshot varies)
- MeTube cookie validity today
- Whether TNAS WoL enabled in TerraMaster firmware
- Radarr path smoke less exercised than Sonarr TV
