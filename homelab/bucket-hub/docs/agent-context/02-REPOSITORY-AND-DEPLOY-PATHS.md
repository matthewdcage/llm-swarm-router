# Repository and deploy paths

## Canonical source (edit here)

All Hub code changes should be made under the monorepo:

```text
llm-swarm-router/
  homelab/bucket-hub/          ← Bucket-HUB application (canonical)
    app.py                     ← Pi Hub API (FastAPI)
    form.html                  ← Download UI + queue (also served as hub-form)
    hub-worker.py              ← Ubuntu worker (main loop)
    hub_torrent_parse.py
    download_destinations.py
    media_pipeline.py
    job_health.py              ← Stuck-job / agent_review detection
    torrent_indexer_catalog.py
    torrent_indexer_urls.py
    llm_ctl.py
    machines_ctl.py
    capture-folders.json       ← Default drive/folder for capture kinds
    research-presets.json      ← Research panel presets (HN, GitHub search, HF)
    overnight-schedule.default.json
    glance.yml                 ← Glance config (deploy to Pi)
    Caddyfile                  ← hub-gateway routes
    docker-compose-media.yml   ← Reference media stack (ai-server)
    scripts/
      overnight-run.py         ← Pi nightly scheduler client
      deploy-hub-jellyfin-proxy.sh
      deploy-torrent-jellyfin-e2e-home.sh
      apply-aria2-hub-tuning.sh
      hub-lan-smoke-test.py
    tests/                     ← pytest (PYTHONPATH=. from bucket-hub dir)
    docs/
      agent-context/           ← This pack
      MANUAL-STEPS.md
      HUB-DOWNLOAD-DESTINATIONS.md
      MEDIA-JELLYFIN-PIPELINE.md
      JOB-HEALTH-AND-AGENT-REVIEW.md
      …
```

Parent repo agent guide: `llm-swarm-router/AGENTS.md` (netllm + workspace rules).

## Runtime paths on Pi (`stackrise-pi`)

Typical layout (user `bucket`):

| Path | Purpose |
|------|---------|
| `~/homelab/hub-form/` | Deployed copy of `app.py`, `form.html`, static assets |
| `~/homelab/glance/` | `glance.yml`, Caddyfile mount for `hub-gateway` |
| `~/homelab/data/hub-jobs/` | `JOBS_DIR` — jobs, library, overnight state, research cache |
| `~/homelab/data/hub-jobs/jobs/*.json` | Individual job records |
| `~/homelab/data/hub-jobs/library.json` | Drives/folders/mount state |
| `~/homelab/data/hub-jobs/research-presets.json` | Optional override of presets |
| `~/homelab/data/hub-jobs/research-cache.json` | Last research results per preset |

Services: `hub-form`, `hub-gateway`, `glance`; tunnel `cloudflared`.

## Runtime paths on Ubuntu (`ai-server`, user `bucket`)

| Path | Purpose |
|------|---------|
| `~/homelab/scripts/hub-worker.py` | Worker (sync from repo) |
| `~/homelab/scripts/hub-worker.env` | **Secrets** — `HUB_WORKER_TOKEN`, `SONARR_*`, `JELLYFIN_*`, optional `GITHUB_TOKEN` |
| `~/homelab/docker-compose.yml` | Gluetun, aria2, Jackett, *arr, Jellyfin, … |
| `~/homelab/aria2/aria2.conf` | Torrent tuning (max concurrent, seed-ratio, continue) |
| `~/homelab/data/jackett/` | Jackett config (`ServerConfig.json` has API key) |
| `~/homelab/logs/hub-worker.log` | Worker stdout |
| `/mnt/external/movies/torrents/` | aria2 complete dir (Expansion HDD) |
| `/mnt/external/movies/tv/` | Sonarr library root on host (`/data/tv` in containers) |
| `/mnt/tnas/` | TNAS mount (may be down) |

## SSH config (maintainer Mac)

| Host alias | Use |
|------------|-----|
| `stackrise-pi` | Pi deploy, `JOBS_DIR`, Caddy |
| `ai-server` | LAN Ubuntu |
| `ai-server-ts` | Tailscale Ubuntu (`100.112.168.40`) |

## Tests (local)

```bash
cd homelab/bucket-hub
PYTHONPATH=. pytest tests/ -q
```

CI for Hub is mostly manual + `scripts/hub-lan-smoke-test.py` on LAN.

## Deploy pattern (maintainer)

1. Edit files under `homelab/bucket-hub/`.
2. `scp`/`rsync` to Pi `~/homelab/hub-form/` and ai-server `~/homelab/scripts/`.
3. Restart `hub-form` / `hub-gateway` on Pi; `systemctl --user restart hub-worker` on ai-server.
4. Bump cache buster in `glance.yml` for `form.html` (`?v=YYYYMMDD…`) after JS/CSS changes.

See `05-OPERATIONS-SSH-DEPLOY.md`.
