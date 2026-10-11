# Operations, SSH, deploy

## Preconditions

- ai-server **awake** (WoL from Hub Machines or overnight step).
- Pi **hub-form** and **hub-gateway** running.
- Worker: `systemctl --user status hub-worker` → **active**.
- For torrents: **Gluetun healthy**, Expansion HDD mounted.

## Quick health (from Mac, Tailscale)

```bash
curl -s http://100.85.42.0:3000/api/status | jq '{vpn_live, heartbeat_age_s, jobs}'
ssh ai-server-ts 'systemctl --user is-active hub-worker'
ssh ai-server-ts 'docker ps --format "{{.Names}} {{.Status}}" | grep -E gluetun|aria2|jellyfin'
```

## Deploy Hub UI + API to Pi

Use `scripts/deploy-manifest.txt` (Pi section) and `scripts/verify-deploy-parity.sh pi`.
Do not hand-write rsync file lists.

```bash
# From llm-swarm-router repo root
cd homelab/bucket-hub
./scripts/verify-deploy-parity.sh pi   # expect mismatch before first AP-1 deploy
# rsync every path in the [pi] section of scripts/deploy-manifest.txt to ~/homelab/hub-form/
# including static/hub-escape.js, job_kinds.py, index_writer.py
ssh stackrise-pi 'sudo systemctl restart hub-form hub-gateway glance'
# bump glance.yml iframe ?v=
./scripts/verify-deploy-parity.sh pi   # must exit 0
```

## Deploy worker to ai-server

```bash
cd homelab/bucket-hub
./scripts/verify-deploy-parity.sh worker
# rsync every path in the [worker] section to ~/homelab/scripts/
# including torrent_indexer_catalog.py, torrent-indexers-catalog.json, job_kinds.py, index_writer.py
ssh ai-server-ts 'systemctl --user restart hub-worker'
./scripts/verify-deploy-parity.sh worker   # must exit 0
```

## Environment files (never commit)

| File | Host | Keys (examples) |
|------|------|-----------------|
| `~/homelab/scripts/hub-worker.env` | ai-server | `HUB_WORKER_TOKEN`, `HUB_BASE_URL`, `SONARR_API_KEY`, `SONARR_URL`, `JELLYFIN_*`, `RADARR_*`, optional `GITHUB_TOKEN` |
| Pi hub-form env | Pi | `HUB_WORKER_TOKEN`, `JOBS_DIR`, `MC_MANAGER_*` |

Template references: `docs/MANUAL-STEPS.md` § hub-worker.env.

## Scripts

| Script | Run where | Purpose |
|--------|-----------|---------|
| `scripts/deploy-torrent-jellyfin-e2e-home.sh` | Mac | Pi + media smoke |
| `scripts/deploy-hub-jellyfin-proxy.sh` | Mac | Caddy Jellyfin proxy |
| `scripts/apply-aria2-hub-tuning.sh` | ai-server | aria2.conf backup + tune |
| `scripts/overnight-run.py` | Pi | Nightly schedule |
| `scripts/hub-lan-smoke-test.py` | LAN | API smoke |

## Logs

| Log | Location |
|-----|----------|
| Worker | `~/homelab/logs/hub-worker.log` on ai-server |
| aria2 | `docker logs aria2` |
| Hub API | Pi `journalctl` / container logs for hub-form |

## QC checklist

`docs/QC-SYSTEM-PROCESSES.md` — job counts, VPN, Jellyfin tags after import.

## When Pi ↔ worker heartbeats timeout

Symptoms: VPN badge stale, progress frozen, `HTTP 400` on worker PATCH. Often transient LAN load; worker log shows `heartbeat failed: timed out`. Hub may still be reachable from Mac via Tailscale. Fix: reduce parallel jobs, restart worker, ensure Pi `:3020` reachable from ai-server.
