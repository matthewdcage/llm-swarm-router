# Hub API and data model

Base URL: `http://<pi>:3020` or via Caddy `http://<pi>:3000/api/…`.

Auth: **Public read** for most UI routes; **worker token** required for `PATCH` jobs, claim, heartbeat, purge complete.

## Core job JSON (storage: `jobs/<id>.json`)

```json
{
  "id": "12-char hex",
  "url": "magnet:… | https://…",
  "title": "string",
  "kind": "magnet | http | video | github | hf | website_archive | sync | request",
  "source": "torrent | capture | …",
  "status": "queued | claimed | running | paused | done | error | review | dismissed | purging",
  "worker": "aria2 | metube | repo | httrack | manual",
  "drive_id": "ext_movies | media_hdd | …",
  "folder_id": "torrents_breaking_bad_season_01 | …",
  "folder_name": "Season 01",
  "destination_path": "/mnt/external/movies/torrents/…",
  "options": {
    "via_proton": true,
    "sensitive": true,
    "high_priority": false,
    "notify_on_complete": false
  },
  "torrent_meta": {
    "show_title_guess": "Breaking Bad",
    "release_kind": "season | full_show | episode",
    "tracker": "LimeTorrents",
    "jf_score": 9
  },
  "progress": { },
  "progress_watch": { "completed": 0, "stall_since": "ISO8601" },
  "aria2_gid": "hex",
  "media_pipeline": { },
  "agent_review": {
    "code": "metadata_stuck",
    "summary": "…",
    "playbook": ["…"],
    "evidence": { }
  },
  "queue_rank": 10,
  "created_at": "ISO8601",
  "started_at": "ISO8601",
  "claimed_by": "ubuntu-worker",
  "error": "string | null"
}
```

Public API adds **`error_display`** (humanized) via `_job_public()`.

## Job endpoints (selection)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/jobs` | List (`limit`, `status`, `tag`) |
| POST | `/api/jobs` | Enqueue (`JobCreate` body) |
| GET | `/api/jobs/{id}` | Single job |
| PATCH | `/api/jobs/{id}` | Worker update (token) |
| POST | `/api/jobs/{id}/pause` | Pause aria2 |
| POST | `/api/jobs/{id}/resume` | Resume (needs gid) |
| POST | `/api/jobs/{id}/retry` | Re-queue failed/done |
| POST | `/api/jobs/{id}/cancel` | Cancel |
| POST | `/api/jobs/queue/reorder` | Full queued id list |
| POST | `/api/jobs/claim` | Worker claim |
| GET | `/api/jobs/agent-review` | Flagged jobs + brief dir path |
| GET | `/api/status` | VPN, job counts, aria2 catalog |
| POST | `/api/worker/heartbeat` | Worker + library merge |
| GET | `/api/library` | Drives/folders for UI |
| POST | `/api/search` | Enqueue search job |
| GET | `/api/torrent/indexers` | Jackett catalog status |
| GET | `/api/requests/summary` | Badge count (`review` status) |
| POST | `/api/requests` | Phone link/note |

Full route list: grep `@app.` in `homelab/bucket-hub/app.py`.

## Library model

- File: `$JOBS_DIR/library.json`
- Drives: `ext_movies`, `media_hdd`, `tnas`, … with `mounted`, `folders[]`.
- Worker heartbeat merges **discovered** USB volumes.

## Worker claim body

Worker polls `POST /api/jobs/claim` with kinds/slots; Pi returns next job respecting `HUB_MAX_CONCURRENT_ARIA2` and priority.

## Agent review artifacts

- On disk: `$JOBS_DIR/agent-review/<job_id>.md`
- Generator: `job_health.agent_review_markdown(job)`

## Error hints

`ERROR_HINTS` + `_human_error()` in `app.py` — map raw worker errors to UI strings (timeouts with partial progress, etc.).
