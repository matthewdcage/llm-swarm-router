# Repository and file paths

## Monorepo location

Root: `llm-swarm-router/` (netllm + homelab). **Bucket-HUB canonical tree:**

```
homelab/bucket-hub/
├── app.py                 # Pi FastAPI Hub API + static routes
├── form.html              # Main Hub UI (download, queue, search) — also /hub-form/
├── hub-worker.py          # Ubuntu worker (deploy copy to ~/homelab/scripts/)
├── hub_torrent_parse.py   # TV season search, pack queries, title parsing
├── download_destinations.py
├── media_pipeline.py      # Sonarr/Radarr add payloads
├── job_health.py          # Stuck-job detection → agent_review (NEW)
├── torrent_indexer_catalog.py
├── torrent_indexer_urls.py
├── torrent-indexers-catalog.json
├── machines.json          # GITIGNORE live — use machines.example.json template
├── machines_ctl.py
├── llm_ctl.py
├── glance.yml             # Glance config (deploy to Pi)
├── glance-hub.css
├── hub-design.css
├── hub-embed.css
├── hub-vpn.js
├── hub-requests.js
├── hub-overnight.js
├── Caddyfile              # Reference; live on Pi under ~/homelab/glance/
├── docker-compose-media.yml  # Reference merge for ai-server
├── capture-folders.json
├── search-providers.json
├── research-presets.json
├── overnight-schedule.default.json
├── requirements-hub-form.txt
├── CHANGES.md             # Audit log for risky changes
├── TODO.md
├── tests/
│   ├── test_job_health.py
│   ├── test_job_concurrency.py
│   ├── test_job_pause.py
│   ├── test_job_queue_reorder.py
│   ├── test_media_pipeline.py
│   ├── test_hub_torrent_parse.py
│   ├── test_aria2_bandwidth_slots.py
│   └── …
├── docs/
│   ├── agent-handoff/     # This pack
│   ├── MANUAL-STEPS.md
│   ├── MEDIA-JELLYFIN-PIPELINE.md
│   ├── HUB-DOWNLOAD-DESTINATIONS.md
│   ├── ARIA2-THROUGHPUT-AND-LEECH.md
│   ├── JOB-HEALTH-AND-AGENT-REVIEW.md
│   └── …
└── scripts/
    ├── deploy-hub-jellyfin-proxy.sh
    ├── deploy-media-stack-ai-server.sh
    ├── deploy-torrent-jellyfin-e2e-home.sh
    ├── apply-aria2-hub-tuning.sh
    ├── hub-torrent-jellyfin-smoke.sh
    ├── hub-lan-smoke-test.py
    ├── overnight-run.py
    ├── mount-watchdog.sh
    └── systemd/…
```

## Runtime paths on Pi

| Path | Purpose |
|------|---------|
| `~/homelab/hub-form/` | Deployed `app.py`, `form.html`, Python modules imported by app |
| `~/homelab/glance/` | `glance.yml`, assets, **Caddyfile** for hub-gateway |
| `/data/hub-jobs/` or `$JOBS_DIR` | `jobs/*.json`, `library.json`, `worker-status.json`, `agent-review/*.md` |
| `systemd --user` | `hub-form.service` → uvicorn on `:3020` |

Environment (typical): `JOBS_DIR`, `HUB_WORKER_TOKEN`, `HUB_MAX_CONCURRENT_ARIA2`, `MC_MANAGER_*` if Minecraft panel enabled.

## Runtime paths on ai-server

| Path | Purpose |
|------|---------|
| `~/homelab/scripts/hub-worker.py` | Worker entry (may import sibling `.py` from same dir) |
| `~/homelab/scripts/hub-worker.env` | `HUB_URL`, `HUB_WORKER_TOKEN`, `SONARR_*`, `JELLYFIN_*`, `JACKETT_*`, aria2 tuning |
| `~/homelab/docker-compose.yml` | Gluetun + aria2 + *arr + Jellyfin + … |
| `~/homelab/aria2/aria2.conf` | `dir=/mnt/torrents`, `max-concurrent-downloads`, `seed-ratio=0` |
| `~/homelab/data/sonarr/` | Sonarr config (`config.xml` has API key) |
| `~/homelab/data/jackett/Jackett/ServerConfig.json` | Jackett API key (local only) |
| `/mnt/external/movies/` | Expansion HDD — `torrents/`, `tv/`, `movies/` |
| `/mnt/media/` | Legacy media HDD mount (some old jobs) |
| `/mnt/tnas/` | TNAS (optional; offline does not block USB Jellyfin) |
| `~/homelab/logs/hub-worker.log` | Worker stdout (if configured) |

## Docker volume mapping (ai-server)

| Container | Host | Container path |
|-----------|------|----------------|
| aria2 | `/mnt/external/movies/torrents` | `/mnt/torrents` |
| sonarr/radarr | `/mnt/external/movies` | `/data` |
| sonarr/radarr | `/mnt/external/movies/torrents` | `/mnt/torrents` |
| jellyfin | `/mnt/external/movies/tv` etc. | `/tv`, `/movies`, … |

## Tests (developer Mac)

```bash
cd homelab/bucket-hub
PYTHONPATH=. python3 -m pytest tests/ -q
# Or from repo root if pytest available via uv
```

## Related repo (not Bucket-HUB)

| Path | Role |
|------|------|
| `llm-swarm-router/packages/*` | netllm mesh router (separate product) |
| `llm-swarm-router/AGENTS.md` | Workspace facts (Hub section is long) |
| `hub-firefox-capture-addon/` | Firefox capture extension (root duplicate) |
| `homelab/bucket-hub/firefox-capture-addon/` | Canonical addon copy |
