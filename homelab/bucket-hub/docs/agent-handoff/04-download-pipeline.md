# Download pipeline (jobs, aria2, worker)

## Job lifecycle

```
queued → claimed → running → done | error | paused | purging → dismissed
```

| Status | Meaning |
|--------|---------|
| `queued` | Waiting for worker slot / claim |
| `claimed` | Worker took job; may not have aria2 gid yet |
| `running` | Active download (aria2 or other worker) |
| `paused` | User paused; worker calls `aria2.pause` |
| `error` | Failed (retryable) |
| `review` | Human inbox (phone notes, text capture) — **not** agent_review |
| `done` | Complete |
| `dismissed` | Hidden from UI, JSON retained |

## Workers (`job.worker` / `kind`)

| worker | kind examples | Backend |
|--------|---------------|---------|
| `aria2` | `magnet`, `http` | aria2 RPC via Gluetun |
| `metube` | `video` | yt-dlp / MeTube |
| `repo` | `github`, `hf` | git / HF CLI |
| `httrack` | `website_archive` | httrack or FlareSolverr crawl |
| `manual` | `request` | No auto download |

## aria2 specifics

### Magnet pipeline

1. Metadata gid (~0.5 MB) may appear first (`phase: metadata`).
2. Data gid via `followedBy` or handoff logic in `wait_aria2` / `_aria2_poll_once`.
3. Worker must **not** mark done on metadata-only complete.

### Paths

- Container `dir=/mnt/torrents` → host `/mnt/external/movies/torrents/`.
- TV: optional subfolder `torrents/<Show Name>/Season NN/` from Hub library API.
- Validation: `validate_aria2_destination()` in `download_destinations.py`.

### Concurrency (env)

| Variable | Host | Default (typical) |
|----------|------|-------------------|
| `HUB_MAX_CONCURRENT_ARIA2` | Pi | 16 |
| `HUB_ARIA2_MAX_ACTIVE` | Worker | 16 |
| `HUB_ARIA2_TARGET_MBPS` | Worker | ~900 |
| `HUB_ARIA2_BOOTSTRAP_SLOTS` | Worker | small burst |

`aria2_dispatch_allowed()` gates new torrents by aggregate Mbps.

### Leech-only

- `ARIA2_LEECH_OPTS` in worker; `seed-ratio=0.0` in `aria2.conf`.
- `_aria2_detach_completed_gid` removes torrent from aria2 after complete (files stay).

### RPC

- URL: `http://127.0.0.1:6800/jsonrpc`
- Secret: `homelab-aria2` (compose `ARIA2_RPC_SECRET`) — **do not publish in docs/commits**.

## User actions (API)

| Action | Endpoint | Notes |
|--------|----------|--------|
| Pause | `POST /api/jobs/{id}/pause` | Requires `aria2_gid` |
| Resume | `POST /api/jobs/{id}/resume` | **Requires valid gid** — broken after aria2 restart |
| Retry | `POST /api/jobs/{id}/retry` | Clears gid unless transient error + reattach |
| Cancel | `POST /api/jobs/{id}/cancel` | Optional `purge: true` |
| Reorder | `POST /api/jobs/queue/reorder` | All queued ids required |

**Operational rule:** After aria2 container restart, use **Retry** (not Resume) to drop stale gids.

## Progress fields (`job.progress`)

| Field | Use |
|-------|-----|
| `phase` | `metadata`, `downloading`, `peers`, … |
| `completed` / `total` | Bytes |
| `seeders`, `connections` | Swarm |
| `speed` | Must request `downloadSpeed` in aria2 `tellStatus` keys |
| `metadata_only` | UI “Getting metadata…” |

UI fallback: byte-delta speed in `form.html` (`impliedDownloadSpeedBps`).

## media_pipeline (post-complete)

```json
{ "sonarr": "added|exists|add_failed|skipped", "radarr": "…", "jellyfin_refreshed": true }
```

Triggered in `notify_media_libraries_after_torrent()` in `hub-worker.py`.

## Job health / agent review (in repo)

See `job_health.py` + `docs/JOB-HEALTH-AND-AGENT-REVIEW.md`.

| Code | Condition |
|------|-----------|
| `metadata_stuck` | Metadata ≥45 min, 0 peers |
| `no_progress` | No byte change ≥90 min |
| `stale_running_complete` | 100% but still running ≥20 min |
| `sonarr_import_failed` | Pipeline add_failed |

## Key functions (worker)

| Function | File | Role |
|----------|------|------|
| `main()` supervisor loop | `hub-worker.py` | Claim slots, aria2 tracks |
| `wait_aria2` / `_aria2_poll_once` | `hub-worker.py` | Progress + handoff |
| `_recover_claimed_aria2_jobs` | `hub-worker.py` | Stuck claimed without gid |
| `ensure_sonarr_series_for_job` | `hub-worker.py` | TV library ensure |
| `jackett_http_json` | `hub-worker.py` | Search relay |

## Static UI

- `form.html` — queue, live strip, search, drag-reorder, torrent chips.
- Cache: `app.py` `max-age=3600` on static — bump `?v=` in `glance.yml` after deploy.
