# Services and integrations

## Hub ↔ Worker

| Item | Detail |
|------|--------|
| Auth | `HUB_WORKER_TOKEN` header on worker `PATCH`/`POST` to Hub |
| Claim | `POST /api/jobs/claim` — Pi enforces `HUB_MAX_CONCURRENT_ARIA2` |
| Heartbeat | `POST /api/worker/heartbeat` — VPN, mounts, drives discovery, **job health rescan** |
| Progress | `PATCH /api/jobs/{id}` — `progress`, `aria2_gid`, `media_pipeline`, `agent_review` |

Worker env: `HUB_URL=http://192.168.50.138:3020` (or Pi TS IP).

## Proton / Gluetun

- All **torrent** and **indexer search** traffic exits via Gluetun.
- Hub header **VPN** widget: live when heartbeat fresh (`vpn_live`) + worker `vpn_ok`.
- Egress test button hits worker-probed IP.
- **Stale VPN badge** ≠ torrent stopped (aria2 still in gluetun netns).

## Jackett (torrent search)

- URL on ai-server: `http://127.0.0.1:9117` (via Gluetun).
- Hub chips: **TV** (anime categories folded in), **Movies**, **Games**.
- TV search: season bar, pack dual-query (`{show} complete` / `{show} season N`) — see `hub_torrent_parse.py`.
- Single-season search: **no** `complete` pass (avoids full-series packs).
- `/dl/` URLs → magnet or torrent file; prefer **torrent file** for dead swarms.
- Catalog: `torrent-indexers-catalog.json` + `docs/TORRENT-INDEXERS-CATALOG.md`.

## SearXNG (web / research)

- Pi relays search to worker → Gluetun.
- Config snippets: `searxng-compose.snippet.yml`, `searxng-settings.yml`.

## MeTube / yt-dlp (YouTube)

- Requires Netscape cookies on ai-server: `~/homelab/data/ytdlp/cookies.txt`.
- Hub video jobs → worker → Gluetun → MeTube.

## Firefox capture

- Addon: `firefox-capture-addon/` — `HUB_CAPTURE_TOKEN`, destination folders from `capture-folders.json`.
- `POST /api/capture` on Hub.

## Sonarr / Radarr / Jellyfin

See **`05-media-jellyfin-sonarr.md`**.

Critical: worker `ensure_sonarr_series_for_job` calls `SONARR_URL` — must be reachable from **where the worker runs** (use `http://127.0.0.1:8989` when Sonarr shares Gluetun network namespace).

## LLM / Chat

- Hub `/chat` → iframe `chat.bucket-hub.stackrise.com.au` → dsh on `:3080`.
- Model: Qwen via vLLM (`ai-server-llm.sh`).
- Overnight: Pi `overnight-run.py` + `overnight-dsh-task.sh` on Ubuntu.

## Glance

- Config: `glance.yml` — cache busters on iframe URLs (`?v=YYYYMMDD…`).
- Machines panel: WoL / SSH suspend via `machines_ctl.py` + `machines.json`.

## Minecraft (optional)

- Manager API proxied from Hub `app.py` — `MC_MANAGER_URL`, token in env.

## Uptime Kuma / monitoring

- Bootstrap: `scripts/uptime-kuma-bootstrap.py` (Pi).

## Cloudflare

- Tunnel to Pi `:3000`; **Access** email allow-list.
- Off-LAN 302 to Access is expected, not “Hub down”.

## Telegram (optional)

- Notify on job complete — `notify` module imported by `app.py` if configured on Pi.
