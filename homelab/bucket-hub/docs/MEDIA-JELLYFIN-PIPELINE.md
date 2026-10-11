# Torrent downloads → Jellyfin

## Short answer

**Yes — Jellyfin library folders must include where the video files live**, but the right design is **not** to treat `torrents/` as the permanent library.

| Folder on Expansion HDD | Role |
|-------------------------|------|
| `torrents/` | aria2 download inbox (Hub magnets land here) |
| `tv/` | Sonarr library root → Jellyfin **TV** library (`/tv` in container) |
| `movies/` | Radarr library root → Jellyfin **Movies** (`/movies`) |

**Jellyfin should scan `tv/` and `movies/`** (and optionally `torrents/` as a **staging** TV/Movies library until Sonarr/Radarr import).

Sonarr/Radarr **hardlink** (recommended) completed downloads from `torrents/` into `tv/` / `movies/` with correct names — download folders stay on disk; Hub does **not** seed (see `docs/ARIA2-THROUGHPUT-AND-LEECH.md`). When a Hub torrent finishes on the Expansion HDD, `hub-worker.py`:

1. **Adds the series or movie to Sonarr/Radarr** if it is missing (monitors only the season(s) inferred from the release title; no indexer search).
2. Runs **DownloadedEpisodesScan** / **DownloadedMoviesScan** on the completed folder under `/data/torrents/...`.
3. Requests a **Jellyfin library refresh** and merges Hub tags.

The Hub queue row can show **In library** when `media_pipeline` reports `sonarr`/`radarr` `added` or `exists`.

## One-time setup on ai-server

### 1. Directory layout

```bash
sudo mkdir -p /mnt/external/movies/{torrents,tv,movies,music}
sudo chown -R bucket:bucket /mnt/external/movies
```

### 2. Docker compose

Copy `homelab/bucket-hub/docker-compose-media.yml` to `~/homelab/docker-compose.yml` (merge with your existing file if needed), then:

```bash
cd ~/homelab && docker compose up -d jellyfin radarr sonarr aria2
```

### 3. Jellyfin libraries (Dashboard → Libraries)

Add or edit:

| Library type | Folders inside container |
|--------------|---------------------------|
| Shows | `/tv`, `/torrents` (staging) |
| Movies | `/movies`, `/torrents` (staging) |

Keep `/media` only if you still serve legacy paths on the old Media HDD mount.

### 4. Sonarr (`http://192.168.50.65:8989`)

- **Settings → Media Management → Root Folders:** `/data/tv`
- **Settings → Download Clients → aria2:** host `127.0.0.1`, port `6800`, RPC secret from `.env`
- **Settings → Media Management → Importing:** **Hardlink** (not Move) so files remain under `torrents/` for cleanup while library paths link in `/tv` / `/movies`.
- **Settings → Download Clients → Remote Path Mappings:**
  - Host: `127.0.0.1`
  - Remote: `/mnt/torrents` (aria2 RPC `dir` in compose)
  - Local: `/data/torrents` (Sonarr container bind)

Hub auto-adds missing series/movies before scan. One-off backfill: add the show in Sonarr if needed, then **DownloadedEpisodesScan** on `/data/torrents/<release-folder>` (e.g. *Breaking Bad* S01 already under `torrents/`).

If Sonarr logs `Folder/File specified for import scan … doesn't exist` while the folder is on the host, the container mount is stale — `docker compose restart sonarr` on ai-server, then re-run the scan.

### 5. Radarr (`http://192.168.50.65:7878`)

- Root folder: `/data/movies`
- Same aria2 client and remote path mapping as Sonarr.

### 6. Hub worker env (`~/homelab/scripts/hub-worker.env`)

```bash
JELLYFIN_URL=http://127.0.0.1:8096
JELLYFIN_API_KEY=<Dashboard → API Keys>
SONARR_URL=http://127.0.0.1:8989
SONARR_API_KEY=<Sonarr → Settings → General>
RADARR_URL=http://127.0.0.1:7878
RADARR_API_KEY=<Radarr → Settings → General>
```

Restart the worker after editing.

## The Office (already in `torrents/`)

1. Open Jellyfin → **Dashboard → Libraries → Scan** (or add `/torrents` as Shows if missing).
2. In Sonarr: add *The Office (US)* → **Wanted → Manual Import** → pick folder under `/data/torrents/The Office…` → import to `/data/tv`.
3. Rescan Jellyfin **TV** library; playback should be under **Shows**, not the raw torrent folder name.

## Hub torrent tags → Jellyfin Tags

Hub search **display** tags (`parseTorrentTags` in `form.html`) are slugged on enqueue (e.g. `x265`, `bluray`, `eng-subs`) and merged with folder chip tags. Optional `torrent_meta` on the job stores `category`, `tracker`, `jf_score`, `eng_subs`, `has_subs`.

After aria2 completes on the Expansion HDD:

1. Worker writes `{download_dir}/.hub-job.json` (job id, tags, meta).
2. Sonarr/Radarr ensure + **DownloadedEpisodesScan** / folder scan (`notify_media_libraries_after_torrent`).
3. Jellyfin library refresh, then `jellyfin_apply_hub_tags` searches items and **merges** Hub tags into Jellyfin **Tags** (does not replace codec/resolution metadata from the file).

| Hub UI label (examples) | Jellyfin Tag slug |
|-------------------------|-------------------|
| x265 / HEVC | `x265` |
| BluRay / WEB-DL | `bluray`, `web-dl` |
| Eng subs chip | `eng-subs` |
| Jackett category | lowercased category string |

Failures in tag apply are logged only; the download job still completes as `done`.

## Related

- `docs/HUB-DOWNLOAD-DESTINATIONS.md` — aria2 paths
- `docker-compose-media.yml` — volume reference
- `scripts/hub-torrent-jellyfin-smoke.sh` — LAN smoke checks
