# Integrations

## Proton VPN / Gluetun

- All worker egress for torrents, GitHub clone, HF, search, research goes through **Gluetun**.
- Hub **VPN** widget runs egress test from worker heartbeat.
- Jackett, aria2, Sonarr, Radarr use `network_mode: service:gluetun`.

**Docs:** `docs/HUB-DOWNLOAD-DESTINATIONS.md`, `docs/ARIA2-THROUGHPUT-AND-LEECH.md`

## aria2

- RPC: `http://127.0.0.1:6800/jsonrpc` on ai-server (token in compose / `homelab-aria2`).
- Download root: host `/mnt/external/movies/torrents` → container `/mnt/torrents`.
- **Leech-only:** `seed-ratio=0`; worker may `aria2.remove` on complete.
- **Concurrency:** `HUB_MAX_CONCURRENT_ARIA2` (Pi), `HUB_ARIA2_MAX_ACTIVE`, `HUB_ARIA2_TARGET_MBPS` (worker).
- **Resume pitfall:** After aria2 restart, old `aria2_gid` on jobs is invalid — use **Retry**, not Resume. Partial files without `.aria2` → aria2 error 13 (`allow-overwrite=false`).

## Jackett

- UI/API on ai-server `:9117` (via Gluetun).
- Catalog: `torrent-indexers-catalog.json`, Hub `/indexers` page.
- Hub TV/movie search chips → worker `add_jackett()` in `hub-worker.py`.
- Prefer **`/dl/` HTTP torrent** over magnet-only when metadata stalls.

## Sonarr / Radarr / Jellyfin

- **Sonarr** root: `/data/tv` → host `/mnt/external/movies/tv` (or configured path).
- **Radarr** root: `/data/movies`.
- aria2 remote path mapping: **`/mnt/torrents`** (not `/downloads`).
- Worker: `ensure_sonarr_series_for_job`, `notify_media_libraries_after_torrent` in `hub-worker.py`; logic in `media_pipeline.py`.
- **Jellyfin:** `JELLYFIN_URL` must include base path `/jellyfin`; libraries `/tv`, `/movies`.
- TV app URL: **ai-server Tailscale/LAN**, not Hub proxy.

**Docs:** `docs/MEDIA-JELLYFIN-PIPELINE.md`, `docs/MANUAL-STEPS.md` §8

## MeTube / YouTube

- Requires Netscape cookies on worker for many videos.
- **Docs:** `ytdlp-cookies-README.md`, `MANUAL-STEPS.md`

## SearXNG

- Pi may front search; worker executes queries through VPN for Hub search panel.

## Machines (WoL / sleep)

- `machines.json` (not in git — use `machines.example.json`) + `machines_ctl.py`.
- Overnight can wake **ubuntu** before `queue_drain` and sleep after.
- Pi is status-only for some targets; TNAS wake-only v1.

## Firefox capture addon

- `hub-firefox-capture-addon/` — sends URLs to Hub capture API with token.
- Folder routing: `capture-folders.json` on Pi.

## Telegram notify

- Optional job-complete notifications (worker/`notify` hook in `app.py`).

## netllm / vLLM (Chat + overnight)

- `llm_ctl.py` on Pi talks to ai-server for GPU LLM toggle.
- **Docs:** `MANUAL-STEPS.md` DeepSeek harness roles.

## Storage inspector

- `/storage` — treemap; worker scan jobs; exact Caddy path (no `/storage*` wildcard stealing Glance).

## Index files for agents

| Integration | Primary code |
|-------------|----------------|
| aria2 paths | `download_destinations.py` |
| Torrent parse/search | `hub_torrent_parse.py`, `hub-worker.py` (Jackett) |
| Sonarr/Radarr payloads | `media_pipeline.py` |
| Jellyfin tags | `hub-worker.py` (`jellyfin_apply_hub_tags`) |
| Job health | `job_health.py` |
