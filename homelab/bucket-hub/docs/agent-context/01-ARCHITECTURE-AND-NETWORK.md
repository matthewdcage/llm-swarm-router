# Architecture and network

## Roles

| Role | Host | Typical name | Notes |
|------|------|--------------|--------|
| **Hub control plane** | Raspberry Pi | `stackrise-pi` | Caddy `hub-gateway`, Glance UI, `app.py` (Hub API), job JSON store |
| **Download worker** | Ubuntu Server (GPU) | `ai-server` | `hub-worker.py`, Docker stack (Gluetun, aria2, Jackett, Sonarr, Radarr, Jellyfin, …) |
| **Gaming / spill LLM** | Windows/Linux PC | `Bucket` | Optional `netllm` peer; not on Hub data path |
| **NAS** | TerraMaster TNAS | `tnas` | Libraries, backups, long-term archive; may be offline |
| **Mac** | Daily driver | — | Deploy SSH, Bambu, Cursor |

## URLs (operator)

| Service | LAN | Tailscale | Public |
|---------|-----|-----------|--------|
| **Hub (Glance + form)** | `http://192.168.50.138:3000` | `http://100.85.42.0:3000` | `https://bucket-hub.stackrise.com.au` (Cloudflare Access) |
| **Hub API** | Same origin `/api/*` | Same | Same (behind Access) |
| **Jellyfin (use for TV/apps)** | `http://192.168.50.65:8096/jellyfin` | `http://100.112.168.40:8096/jellyfin` | Do not use Hub proxy for TV clients |
| **Sonarr/Radarr** | Via Gluetun on ai-server `:8989` / `:7878` | SSH tunnel or LAN only | Not exposed publicly |

Base URL **`/jellyfin`** on Jellyfin is required (Dashboard → Networking).

## Request flow (downloads)

```text
Browser / phone / agent
    → Pi :3000  hub-gateway (Caddy)
        → /api/*     → app.py :3020 (hub-form)
        → /          → Glance
        → /hub-form/* → form.html embed (overview queue)

Pi job store (JSON)  ←PATCH/claim—  hub-worker (ai-server)
                           ↓
                    Gluetun (Proton VPN)
                           ↓
              aria2 | git clone | yt-dlp | httrack | HF hub
                           ↓
              /mnt/external/movies (Expansion HDD) | /mnt/tnas | /mnt/media
```

## Fail-closed VPN

If Gluetun is down or worker heartbeat reports `vpn_ok: false`, sensitive jobs should not start (worker checks `vpn_ok()`). Hub header **VPN** badge: stale if worker heartbeat **>25s**.

## Hub components on Pi

- **`hub-gateway`** — Caddy; routes `/api/*` to Hub backend, `/hub-form/` to form, `/storage` exact path, `/jellyfin` proxy optional.
- **`glance`** — Overview dashboard; embeds Hub iframe `?panel=hub`.
- **`hub-form`** — FastAPI `app.py` (systemd or container; listen **3020** behind Caddy).

## Worker stack on ai-server (Docker Compose)

Typical services (`~/homelab/docker-compose.yml`):

- **gluetun** — Proton; shared network for aria2, Jackett, *arr.
- **aria2** — Torrents; host bind `…/torrents` → container `/mnt/torrents`.
- **jackett** — Indexers; API `:9117` on Gluetun network.
- **sonarr / radarr / lidarr** — Import paths under `/data/tv`, `/data/movies`.
- **jellyfin** — Host network or published `:8096`; libraries `/tv`, `/movies`, etc.
- **MeTube / searxng** — YouTube and web search (worker-mediated).

Worker process: **`systemctl --user hub-worker.service`** (not in Docker); reads `~/homelab/scripts/hub-worker.env`.

## Job storage

- Pi: **`JOBS_DIR`** default `/data/hub-jobs` (or `~/homelab/data/hub-jobs` on some installs).
- Per-job files: `jobs/<id>.json`.
- Worker status: `worker-status.json`.
- Agent review briefs (when enabled): `agent-review/<id>.md`.

## LLM (optional)

- Hub **Chat** → iframe → **dsh** on ai-server `:3080` → **netllm** → vLLM.
- Overnight **research** / log review steps call `overnight-dsh-task.sh` (see `MANUAL-STEPS.md`).
