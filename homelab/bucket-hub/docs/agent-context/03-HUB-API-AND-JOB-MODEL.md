# Hub API and job model

Base URL: `http://<pi>:3000` (or Tailscale / public host). JSON unless noted.

**Auth:** Public read/write for LAN/Access users. Worker routes require header **`X-Worker-Token: <HUB_WORKER_TOKEN>`** (set on Pi and in `hub-worker.env`).

## Core endpoints (downloads)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/status` | Worker heartbeat age, VPN live, job counts |
| `GET` | `/api/jobs` | List jobs (`limit`, `status`, `tag`) |
| `GET` | `/api/jobs/{id}` | Single job |
| `POST` | `/api/jobs` | Enqueue job (`JobCreate` body) |
| `PATCH` | `/api/jobs/{id}` | Worker updates status, progress, `aria2_gid`, `media_pipeline`, `agent_review` |
| `POST` | `/api/jobs/{id}/pause` | Pause aria2 job |
| `POST` | `/api/jobs/{id}/resume` | Resume (requires existing `aria2_gid`) |
| `POST` | `/api/jobs/{id}/cancel` | Cancel; optional `{"purge": true}` |
| `POST` | `/api/jobs/{id}/retry` | Re-queue from `error`/`dismissed`/`done` |
| `POST` | `/api/jobs/queue/reorder` | `{"ordered_ids": [...]}` all queued ids |
| `POST` | `/api/jobs/{id}/dismiss` | Hide review/error/queued/**done** from UI |
| `POST` | `/api/jobs/dismiss-queue` | Bulk dismiss review/error/queued |
| `GET` | `/api/jobs/agent-review` | Jobs with `agent_review` set |
| `POST` | `/api/worker/heartbeat` | Worker mount/VPN discovery |
| `GET` | `/api/library` | Drives and folders for enqueue UI |
| `GET` | `/api/devops/log` | Tail of worker dev-ops log |

## Job kinds (`kind` field)

| kind | worker | Behaviour |
|------|--------|-----------|
| `http` | aria2 | HTTP(S) URL; Jackett `/dl/` torrent files |
| `magnet` | aria2 | Magnet URI; metadata gid then data gid (`followedBy`) |
| `video` | metube/yt-dlp | YouTube etc. via Gluetun |
| `github` | git | `git clone --depth 1` via Gluetun into `destination_path/<repo>` |
| `hf` | HF hub | Whole repo or file list |
| `website_archive` | httrack / FlareSolverr | TNAS `Website Backups/` |
| `sync` | rsync | Drive-to-drive sync jobs |
| `request` | manual | Phone **Requests** notes (`status: review`) |

Worker entry: `hub-worker.py` → `process()` / aria2 supervisor.

## Job statuses

| status | Meaning |
|--------|---------|
| `queued` | Waiting for worker slot |
| `claimed` | Worker took job; starting aria2/git |
| `running` | Active |
| `paused` | User paused; worker stops polling |
| `done` | Success |
| `error` | Failed (`error`, `error_display` for UI) |
| `review` | Human approval (Requests inbox) |
| `dismissed` | Hidden from default lists |
| `purging` | Cancel with file removal in progress |

## Enqueue example (GitHub repo)

```http
POST /api/jobs
Content-Type: application/json

{
  "url": "https://github.com/org/repo",
  "title": "org/repo",
  "kind": "github",
  "drive_id": "nas",
  "folder_id": "inbox",
  "tags": ["github", "daily-pull"],
  "high_priority": false,
  "source": "github"
}
```

Resolve `drive_id` / `folder_id` from `GET /api/library` (mounted drives only). Defaults for capture kinds: `capture-folders.json`.

## Enqueue example (torrent via Jackett URL)

```json
{
  "url": "http://127.0.0.1:9117/dl/...",
  "kind": "http",
  "drive_id": "ext_movies",
  "folder_id": "torrents_breaking_bad_season_01",
  "source": "torrent",
  "torrent_meta": { "show_title_guess": "Breaking Bad", "release_kind": "season" }
}
```

Worker resolves Jackett on ai-server; URL in job may use `127.0.0.1:9117` when enqueued from worker search UI.

## Progress object (aria2)

Common fields: `phase` (`metadata` | `downloading` | …), `completed`, `total`, `speed`, `seeders`, `connections`, `metadata_only`, `gid`.

## `media_pipeline` (after torrent complete)

```json
{ "sonarr": "added|exists|add_failed|skipped", "radarr": "...", "jellyfin_refreshed": true }
```

Sonarr API from worker must reach **`http://127.0.0.1:8989`** inside Gluetun network (not LAN IP from host-only curl).

## `agent_review` (stuck jobs)

Set by `job_health.py` when metadata stalls, no progress, etc. See `docs/JOB-HEALTH-AND-AGENT-REVIEW.md`.

## Search and research (not the same as clone jobs)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/search` | Multi-provider search (relays to worker + Gluetun) |
| `GET` | `/api/research/presets` | Preset definitions |
| `POST` | `/api/research` | Run preset; caches in `research-cache.json` |
| `GET` | `/api/research?preset=` | Cached results |

Research uses **`research-presets.json`** and worker `run_private_research()` — GitHub **search API**, not clone.

## Overnight

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/overnight/schedule` | Current schedule |
| `GET` | `/api/overnight/last-night` | Last run summary for UI |
| `GET` | `/api/overnight/status` | Runner state |

Runner script: `homelab/bucket-hub/scripts/overnight-run.py` (often Pi systemd timer).

## Implementation reference files

- API models and routes: `homelab/bucket-hub/app.py`
- Worker claim/dispatch: `hub-worker.py` (`main`, `_dispatch_claimed_job`, `process`)
- UI fetch helpers: `homelab/bucket-hub/form.html` (`hubUrl`, `renderQueueRow`, …)
