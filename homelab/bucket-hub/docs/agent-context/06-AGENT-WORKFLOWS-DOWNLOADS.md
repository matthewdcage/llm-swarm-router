# Agent workflows — recurring downloads

This doc is for an **automation agent** enqueueing work via the Hub API (or proposing code that does).

## Standard enqueue flow

1. `GET /api/library` — confirm target `drive_id` / `folder_id` is **mounted** and `present: true`.
2. `POST /api/jobs` with correct `kind`, `url`, `title`, `tags` (use tags for automation traceability, e.g. `daily-github`, `agent-2026-10-11`).
3. Poll `GET /api/jobs/{id}` or `GET /api/jobs?status=running` until `done` or `error`.
4. On `error`, read `error_display` and `agent_review`; follow playbook in `docs/JOB-HEALTH-AND-AGENT-REVIEW.md`.
5. For torrents destined for Jellyfin, confirm `media_pipeline.sonarr` / `radarr` not `add_failed`.

## Idempotency

- Hub does **not** dedupe by URL by default. Daily jobs should:
  - Tag consistently (`daily-github`, repo slug).
  - Skip enqueue if a `done` job with same URL exists in last N days (agent logic), **or**
  - Use fixed destination `…/github-daily/owner-repo` and let `git clone` fail if exists (worker does not auto-pull — **clone only**).

**Gap to implement for daily GitHub:** `git pull` or wipe-and-clone policy (see `07-DAILY-GITHUB-TOP-REPOS.md`).

## Priority and slots

- `high_priority: true` on `POST /api/jobs` — wakes ai-server (WoL) and sorts ahead in queue.
- Only **N** aria2 jobs active (`HUB_ARIA2_MAX_ACTIVE`, bandwidth cap). Queue others show **waiting for worker**.

## Torrent best practices

- Search in Hub UI → queue with Jackett **torrent file** when possible.
- TV: use season-specific search (`Show season 1`) not bare show name (avoids full-series packs).
- Do not resume after aria2 session loss without **Retry** (clears dead gid).
- Partial + error 13: need folder cleanup or worker `allow-overwrite` handling.

## Phone / Requests workflow (human)

- `POST /api/requests` with `mode: note` → `status: review` (shared inbox bell).
- Not the same as `agent_review` on stuck downloads.

## Reporting back to maintainer agent

Include in handoff:

- List of job ids created
- `GET /api/jobs/agent-review` if any flags
- Proposed code paths under `homelab/bucket-hub/`
- Deploy steps from `05-OPERATIONS-SSH-DEPLOY.md`
- Tests: `pytest tests/test_*.py` relevant modules

## API discovery

Full route list: search `@app.` in `homelab/bucket-hub/app.py`.
