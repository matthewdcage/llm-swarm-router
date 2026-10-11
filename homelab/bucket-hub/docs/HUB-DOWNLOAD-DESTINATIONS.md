# Hub download destinations (aria2 / torrents)

Audit reference for **where torrents and direct HTTP downloads may land**, and how Pi + ai-server stay aligned.

## Problem this solves

Jobs can be **queued** with a `destination_path` the ubuntu-worker **cannot map into the aria2 container**. The job then fails at claim time with:

```text
unsupported destination_path: /mnt/external/movies
```

That looks like a “stuck” or “broken” download even though the magnet was valid. Root cause is a **contract mismatch** between:

1. Hub **library** paths (what the UI offers), and  
2. **aria2 `docker-compose` volumes** + `download_destinations.py` on ai-server.

## Architecture

| Layer | Role |
|-------|------|
| **`download_destinations.py`** | Single list of allowed host prefixes + mapping to aria2 `dir` inside Gluetun |
| **Hub `app.py`** | Rejects `POST /api/jobs` (and aria2 **retry**) if destination is not in the catalog |
| **`hub-worker.py`** | Re-validates at run time (mount checks via `_safe_check_path`) |
| **`GET /api/worker/aria2-destinations`** | Machine-readable catalog for UI/docs |
| **`GET /api/status`** | Includes `aria2_destinations` for dashboards |

Sync jobs (`kind: sync`) and MeTube/yt-dlp jobs use **other** path rules — this doc is **aria2 only**.

## Allowed destinations (current)

| ID | Host prefix | aria2 container path | Compose on ai-server |
|----|-------------|----------------------|----------------------|
| `media_hdd` | `/mnt/media` | `/media/…` | `/mnt/media:/media` |
| `movies_usb` | `/mnt/external/movies` | `/mnt/torrents` (bind = host `…/torrents/`) | `/mnt/external/movies/torrents:/mnt/torrents` |
| `tnas_staging` | `/mnt/tnas/…` | `/media/_nas_staging/…` then copy to TNAS | `/mnt/media:/media` + staging dir |

### Movies / torrents detail

When the user picks **Movies (entire drive)** in the Hub, `destination_path` is `/mnt/external/movies`. The worker:

1. Ensures `/mnt/external/movies/torrents` exists on the host.  
2. Tells aria2 to use `dir=/mnt/torrents` (bind-mounted to that folder).

**Do not use `/downloads` inside p3terx/aria2-pro** unless you verify the bind mount is the real path: the image can leave `/downloads` on container overlay (ext4, root-owned). Symptom: `Permission denied` on mkdir while host `bucket` can create the same path. Fix: bind to `/mnt/torrents` and set `dir=/mnt/torrents` in `~/homelab/aria2/aria2.conf`, then `docker compose up -d --force-recreate aria2`. Confirm with:

```bash
docker exec aria2 stat -c '%d' /mnt/torrents
stat -c '%d' /mnt/external/movies/torrents   # same device id as host exFAT
docker exec -u p3terx aria2 touch /mnt/torrents/.write_test
```

Radarr/Sonarr library paths (`movies/`, `tv/`) are separate from the **torrent client download** folder.

### TV torrents → Sonarr → Jellyfin

Hub does **not** queue directly into `/tv` (Sonarr owns library paths). For TV/anime torrents:

1. Hub auto-selects the **Expansion HDD** (Movies) drive and optional subfolder `torrents/<Show Name>/`.
2. aria2 completes under host `/mnt/external/movies/torrents/…`.
3. **Sonarr** imports from `/data/torrents/…` into `/data/tv/<Show>/Season …`.
4. **Jellyfin** library **TV Shows (Sonarr)** scans `/tv`.

Optional per-show folders under `torrents/` only organise the inbox before import; they do not replace Sonarr’s final layout.

## Adding a new download target

1. Add a row to `ARIA2_DESTINATION_RULES` in `download_destinations.py` (Pi **and** worker copy).  
2. Extend `aria2_dir_for()` in the same file.  
3. Add matching **aria2 volume** in `~/homelab/docker-compose.yml` on ai-server; `docker compose up -d aria2`.  
4. Deploy `hub-worker.py` + `download_destinations.py` to `~/homelab/scripts/`.  
5. Restart hub-form on Pi if `app.py` changed.  
6. Add a row to **CHANGES.md** (audit log).  
7. Smoke: `curl -s http://192.168.50.138:3020/api/worker/aria2-destinations`

## QC checks

```bash
# Catalog
curl -s http://192.168.50.138:3020/api/worker/aria2-destinations | python3 -m json.tool

# Reject bad path at enqueue (expect HTTP 400 + clear message)
curl -s -X POST http://192.168.50.138:3020/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"url":"magnet:?xt=urn:btih:dead","kind":"magnet","drive_id":"nas","folder_id":"inbox"}' | python3 -m json.tool

# On ai-server: aria2 sees torrents mount (same device as host)
ssh bucket@100.112.168.40 'docker exec aria2 ls -la /mnt/torrents | head'
```

## Magnets vs Jackett torrent URLs

| Path | Behaviour |
|------|-----------|
| `magnet:?xt=urn:btih:…` | aria2 metadata job (~0.5–0.7 MB) then **`followedBy`** data gid (~100+ GB). Worker waits on `followedBy` / active gid with `min_total_bytes`, not metadata completion alone. |
| Jackett `http://127.0.0.1:9117/dl/…` | Often **302 → magnet** (no `.torrent` body). Worker resolves `Location:` with `wget -S` on the host, then `addUri` magnet. Do not pass Jackett `/dl/` URLs to aria2 RPC directly (container cannot reach host `127.0.0.1:9117`; `max-file-not-found`). |
| Nested Hub folders `torrents/Show/Season …` | For aria2 jobs under `…/torrents`, worker uses **flat** `dir=/mnt/torrents` so release folders are not double-nested (exFAT path length + mkdir failures). |

Duplicate infohash: `_aria2_add_uri_magnet` reuses an **active/waiting** gid before re-adding; parses infohash from aria2 error text if needed.

## Incident: 2026-10-03 Office torrents

| Job | Destination | Failure | Fix |
|-----|-------------|---------|-----|
| REMUX ~900 GB | `/mnt/external/movies` | `unsupported destination_path` | Added `movies_usb` rule + compose mount |
| x265 ~150 GB | same | same | Retry after deploy |

**UI:** torrent search rows include **Download now** (high priority). **Prevention:** enqueue-time validation (this document).

## Incident: 2026-10-09 Office S01–S09 (HiQVE)

| Symptom | Cause | Fix |
|---------|--------|-----|
| `Magnet finished after metadata only` at ~695 KB | Worker treated metadata gid as complete before `followedBy` data job | `wait_aria2` polls `followedBy` + active large-total gid |
| `Permission denied` mkdir under `/downloads/…` | aria2 container `/downloads` was overlay ext4, not Expansion HDD bind | Bind `:/mnt/torrents`, `dir=/mnt/torrents`, recreate aria2 |
| Hub VPN **stale** / fail-closed during download | `wait_aria2` blocked worker loop; heartbeats only between jobs | Heartbeat every 12s in `wait_aria2` (Gluetun was still healthy) |
| Jackett torrent-file job errors | `/dl/` → magnet; aria2 could not fetch URL | Host `wget -S` + magnet `addUri` |

Reference job after fix: `45f211bc4d47` (Jackett → magnet, `ext_movies` / `torrents`).

**Sonarr/Radarr:** update aria2 remote path mapping from `/downloads` to **`/mnt/torrents`** if imports stall.

## Throughput baseline (ai-server, 2026-10-10)

Captured during an active Expansion HDD torrent (Office HiQVE pack):

| Signal | Typical observed value | Notes |
|--------|------------------------|--------|
| aria2 `downloadSpeed` | ~1–3 MiB/s | Swarm-limited (~2 seeders, few connections), not a Hub software cap |
| `max-overall-download-limit` | `0` (unlimited) | From `~/homelab/aria2/aria2.conf` |
| `bt-max-peers` | `128` | Per-torrent peer ceiling |
| `max-connection-per-server` / `split` | `32` / `64` | HTTP-style piece sources |
| `file-allocation` | `none` | Required for exFAT Expansion HDD |
| `disk-cache` | `64M` | aria2.conf global |
| Proton port forwarding | `VPN_PORT_FORWARDING=on` | Gluetun compose |
| Per-job aria2 `dir` | `/mnt/torrents` | Hub RPC opts override stale `dir=/downloads` in template conf |

Low speed on a single huge torrent usually means **peers/VPN**, not queue serialisation. Hub **concurrent aria2** improves **queue wall-clock** when multiple swarms can use the link together.

**Bandwidth-aware slots (ai-server worker):** the supervisor adds torrents while aggregate `downloadSpeed` is below a line cap, up to a hard max count.

| Variable | Default | Role |
|----------|---------|------|
| `HUB_ARIA2_MAX_ACTIVE` | `16` | Hard cap on simultaneous torrents |
| `HUB_ARIA2_TARGET_MBPS` | `600` | Stop starting new torrents when sum of active speeds reaches ~90% of this (decimal Mbps). `0` = ignore bandwidth, fill to max active only |
| `HUB_ARIA2_BOOTSTRAP_SLOTS` | `4` | Always allow this many slots while magnets connect (speed still near zero) |
| `HUB_ARIA2_TARGET_HEADROOM` | `0.90` | Fraction of target Mbps before dispatch pauses |
| `HUB_MAX_CONCURRENT_ARIA2` | `16` | Pi claim guard — should be ≥ worker max active |

### Recommended aria2.conf checks (ops)

Full checklist, leech-only policy, and `apply-aria2-hub-tuning.sh`: **`docs/ARIA2-THROUGHPUT-AND-LEECH.md`**.

- Keep `file-allocation=none` on exFAT; do not enable `falloc`.
- Ensure `max-overall-download-limit=0` unless you intentionally cap WAN usage.
- Align `max-concurrent-downloads` with Hub (`16`); aria2 **5** blocked a 6th torrent even when the worker allowed more.
- Leech-only: `seed-ratio=0.0`, `seed-time=0`; Hub worker removes the gid on complete (files stay for Sonarr).
- Optional: raise `bt-max-peers` toward `200` only after measuring CPU/USB load with many active torrents.
- Recreate aria2 after conf changes: `docker compose up -d --force-recreate aria2` on ai-server.

## Hub concurrency env (Pi + worker)

| Variable | Default | Where |
|----------|---------|--------|
| `HUB_MAX_CONCURRENT_ARIA2` | `16` | Pi `hub-form` — claim guard |
| `HUB_ARIA2_MAX_ACTIVE` | `16` | ai-server `hub-worker.env` — supervisor slot ceiling |
| `HUB_ARIA2_TARGET_MBPS` | `600` | ai-server — aggregate download cap for starting new torrents |
| `HUB_SLOT_VIDEO` | `1` | worker — parallel yt-dlp jobs |
| `HUB_SLOT_ARCHIVE` | `0` | worker — `0` = no website archive while torrents run |

Rollback serial behaviour: set both aria2 vars to `1`.

## Related files

- `download_destinations.py` — rules  
- `hub-worker.py` — `run_aria2_job`  
- `app.py` — `create_job`, `retry_job`, `/api/status`  
- `docs/QC-SYSTEM-PROCESSES.md` — operational checklist  
- `CHANGES.md` — dated audit rows  
