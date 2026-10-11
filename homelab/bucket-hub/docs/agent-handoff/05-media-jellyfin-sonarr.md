# Media: Jellyfin, Sonarr, Radarr

Full runbook: **`../MEDIA-JELLYFIN-PIPELINE.md`**.

## Intended flow

1. Hub queues torrent → aria2 completes under **`/mnt/external/movies/torrents/…`**.
2. Worker **`ensure_sonarr_series_for_job`** / **`ensure_radarr_movie_for_job`** if missing.
3. Worker **`DownloadedEpisodesScan`** on `/data/torrents/<release-folder>`.
4. Sonarr **hardlinks** into `/data/tv/<Show>/Season N/`.
5. Worker **`JELLYFIN_URL/Library/Refresh`** + tag merge (`jellyfin_apply_hub_tags`).

## Jellyfin libraries

| Library name (typical) | Container path | Content |
|------------------------|----------------|---------|
| **TV Shows (Sonarr)** | `/tv` | Imported series |
| **TV Show** (legacy) | `/media/...` | Old mount — do not confuse with Sonarr |
| **Movies (Radarr)** | `/movies` | |
| **Movies** / staging | `/torrents` | Optional until import |

**TV app:** `http://<ai-server>:8096/jellyfin` (TS: `100.112.168.40`).

**Browser:** `/jellyfin/web/` — Hub proxy at `http://<pi>:3000/jellyfin/web/` adds hop; prefer direct for playback.

**Firefox HEVC:** disable `EnablePlaybackRemuxing` — force H.264 transcode.

## Sonarr settings checklist

- Root folder: `/data/tv`
- Season folders: **on** (episode linking)
- Importing: **Hardlink**
- aria2 client: `127.0.0.1:6800`, RPC secret
- Remote path mapping: remote `/mnt/torrents` → local `/data/torrents`
- API from worker container network: `http://127.0.0.1:8989` + `X-Api-Key`

Manual import fix (when auto-scan fails):

```bash
docker exec sonarr curl -sS -X POST 'http://127.0.0.1:8989/api/v3/command' \
  -H "X-Api-Key: $SONARR_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name":"DownloadedEpisodesScan","path":"/data/torrents/<ReleaseFolder>"}'
```

## hub-worker.env (template)

```bash
HUB_URL=http://192.168.50.138:3020
HUB_WORKER_TOKEN=<shared-secret>
JELLYFIN_URL=http://127.0.0.1:8096/jellyfin   # include /jellyfin if Base URL set
JELLYFIN_API_KEY=<from Jellyfin dashboard>
SONARR_URL=http://127.0.0.1:8989
SONARR_API_KEY=<from Sonarr config.xml>
RADARR_URL=http://127.0.0.1:7878
RADARR_API_KEY=<from Radarr>
```

Restart: `systemctl --user restart hub-worker`.

## Sidecar

- `{download_dir}/.hub-job.json` — tags + `torrent_meta` for Jellyfin tag sync.

## Common failures

| Symptom | Cause | Fix |
|---------|--------|-----|
| Files in `torrents/` but empty `/tv/Show` | Scan never ran or Sonarr missing series | Manual `DownloadedEpisodesScan`; add series |
| `add_failed` in pipeline | Worker cannot reach Sonarr API on wrong URL | Fix `SONARR_URL`; use docker exec curl |
| Jellyfin empty after import | Library refresh not run | `POST …/Library/Refresh` with API key |
| Episode orphans | Flat import without season folders | Enable season folders; re-import |

## Smoke script

`homelab/bucket-hub/scripts/hub-torrent-jellyfin-smoke.sh` (LAN).
