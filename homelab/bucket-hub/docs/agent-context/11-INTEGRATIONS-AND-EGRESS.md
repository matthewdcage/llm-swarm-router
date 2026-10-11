# Integrations and egress policy

Extends [`04-INTEGRATIONS.md`](04-INTEGRATIONS.md) for audit depth. **Policy:** user-facing download, search, git, HF, research, and torrent indexers egress via **Gluetun (Proton)** on ai-server. Pi Hub **orchestrates** (job JSON, UI, relay); Pi does not scrape the public internet for those workloads.

## Host roles

| Host | Role |
|------|------|
| Raspberry Pi | `hub-form` :3020, Caddy :3000, Glance, job store, machines WoL, optional SearXNG front |
| ai-server (ubuntu) | `hub-worker.py`, Docker stack behind Gluetun |
| TNAS | Optional library + Website Backups + Seedvault target |
| Mac / Bucket | WoL/sleep targets; not download workers |

## Gluetun / VPN

- Worker heartbeat exposes `vpn_live`, egress IP, age
- Hub UI VPN test triggers worker-side check
- Stale heartbeat (>25s) during long aria2 polls was a **worker loop** issue (fixed by heartbeats in wait) — not always VPN down
- Jackett, aria2, Sonarr, Radarr, FlareSolverr: `network_mode: service:gluetun`

## aria2

- RPC `127.0.0.1:6800`, secret in compose (name only in docs: `ARIA2_RPC_SECRET`)
- Leech-only; bandwidth-gated concurrency — see [`ARIA2-THROUGHPUT-AND-LEECH.md`](../ARIA2-THROUGHPUT-AND-LEECH.md)
- exFAT Expansion HDD: `file-allocation=none`

## Jackett + SearXNG

- Jackett `:9117` — TV/Movies/Games search; `/dl/` torrent files preferred over bare magnets
- Catalog: `torrent_indexer_catalog.py`, `/indexers` UI, `verify-jackett-catalog.sh`
- SearXNG: search providers in `search-providers.json`; worker relay for Hub search modes

## Sonarr / Radarr / Jellyfin

See [`08-TV-AND-MEDIA-PIPELINE.md`](08-TV-AND-MEDIA-PIPELINE.md).

## MeTube / YouTube

- `kind: video` via worker; Netscape cookies required for many videos
- Regressions documented in `TODO.md` changelog — live cookie health unknown until probe

## Machines (`machines_ctl.py`)

| Machine | Wake (WoL) | Sleep (SSH) |
|---------|------------|-------------|
| TNAS | Yes | **No** (v1) |
| ubuntu | Yes | Yes (`systemctl suspend`) |
| mac, bucket | Yes | Yes |
| pi | No (hub host) | No |

- Config: `~/homelab/data/hub-jobs/machines.json` on Pi (**not in git** — template `machines.example.json`)
- Sleep from Pi needs `MACHINES_SSH_IDENTITY` on hub-form
- Overnight: `wake-tnas`, `wake-ubuntu`, `sleep-ubuntu` in `overnight-schedule.default.json`

## Firefox capture

- Addon posts to Hub with `HUB_CAPTURE_TOKEN`
- `capture-folders.json` — route file types to destinations (e.g. YouTube → TNAS media)

## Minecraft

- Hub panel: on/off/map — **not** job queue; separate manager API proxy in `app.py`

## LLM (see also 13)

- `llm_ctl.py` — toggle vLLM on ai-server for Chat
- Orthogonal to **netllm** mesh in parent repo unless explicitly wired

## Storage inspector

- Worker scan + `GET /api/library` drives
- TNAS scans when mounted; offline → red widget, torrents still work on USB

## Coordinator / outreach (parent repo)

- `.cursor/coordinator/` — **gitignored**, local maintainer only; not in Hub runtime path
