# Job health and agent review

Hub downloads can look “fine” in the UI while aria2 is stuck (metadata with no peers, worker heartbeats timing out, Sonarr never importing). This flow **flags** those jobs for humans and Cursor agents without auto-canceling work.

## Detection (worker + Pi heartbeat)

Rules live in `job_health.py` (env-tunable):

| Code | When |
|------|------|
| `metadata_stuck` | Metadata phase, ≥ `HUB_METADATA_STUCK_MINUTES` (default 45), 0 seeders and 0 connections |
| `no_progress` | Downloading, no byte change for ≥ `HUB_NO_PROGRESS_STUCK_MINUTES` (default 90) |
| `stale_running_complete` | `completed ≥ total > 0` but still `running` for ≥ `HUB_STALE_COMPLETE_MINUTES` (default 20) |
| `sonarr_import_failed` | `media_pipeline.sonarr == add_failed` while still running |

The worker updates `progress_watch` on each aria2 poll and PATCHes `agent_review` when a rule fires. The Pi also re-scans running jobs on **worker heartbeat** so flags appear even if progress PATCHes are slow.

## Job fields

```json
{
  "agent_review": {
    "flagged_at": "2026-10-11T…",
    "code": "metadata_stuck",
    "title": "Metadata stall (no peers)",
    "summary": "…",
    "evidence": { … },
    "playbook": ["…"],
    "doc_refs": ["homelab/bucket-hub/docs/…"]
  }
}
```

Status stays `running` / `paused` — the job is not moved to phone **Requests** (`review`). UI shows **Needs review** on the queue strip when `agent_review` is set.

## Agent artifacts (Pi)

When `agent_review` is set, the Hub writes:

`$JOBS_DIR/agent-review/<job_id>.md`

List flagged jobs:

```bash
curl -s http://127.0.0.1:3020/api/jobs/agent-review | jq .
```

For Cursor: read the markdown files under the Pi jobs volume or call the API, then follow the playbook (often: Jackett `.torrent` re-queue, Sonarr scan via `docker exec sonarr`, fix `SONARR_URL` in `hub-worker.env`).

## Training Day pattern

Magnet-only releases with **one seeder** on Jackett often never complete metadata. **Fix:** cancel → search again → queue the **HTTP `/dl/`** torrent row with healthy seeders (same approach as Breaking Bad S01 MRSK → 10bit COMPLETE pack).

## Clearing flags

When progress recovers (peers connect, bytes move, job completes), the worker clears `agent_review`. Dismiss/cancel/remove the job deletes the markdown brief.
