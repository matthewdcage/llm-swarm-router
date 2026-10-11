# Daily pull — top GitHub repos

Goal: **once per day**, discover high-signal GitHub repositories and **clone** them into NAS (or configured drive) via the existing Hub **`kind: github`** pipeline.

## What exists today

| Capability | Location | Limit |
|------------|----------|--------|
| **Clone job** | `hub-worker.py` → `run_github_job()` | `git clone --depth 1` through Gluetun; no pull/update if dir exists |
| **GitHub search (discovery only)** | Research presets + worker search | Returns URLs; does **not** auto-enqueue clones |
| **Default folder for github** | `capture-folders.json` | `nas` / `inbox` (override via library) |
| **Overnight queue drain** | `overnight-run.py` step `queue_drain` | Processes whatever is queued; no GitHub-specific step |

There is **no** built-in “daily trending” scheduler yet — implement one of the options below.

## Recommended architecture (two-phase)

### Phase A — Discovery (daily)

Use **GitHub Search API** (authenticated) for predictable rate limits:

```text
GET https://api.github.com/search/repositories
  ?q=created:>YYYY-MM-DD+sort:stars-desc
  &per_page=30
```

Or “rising” repos: `stars:>100 pushed:>YYYY-MM-DD` (tune queries).

**Where to run:** ai-server through Gluetun (same as worker search) — either:

1. **Extend research presets** — add preset `github_daily` in `research-presets.json`:

```json
{
  "id": "github_daily",
  "label": "GitHub daily (stars)",
  "description": "New repos from last 24h sorted by stars",
  "sources": [
    {
      "type": "github",
      "query": "created:>@yesterday sort:stars-desc",
      "per_page": 25
    }
  ]
}
```

Note: worker may need a **placeholder** for date (today `@yesterday` is not built-in — implement `created:>2026-10-10` in script or extend worker to substitute dates).

2. **New script** (cleanest for automation):  
   `homelab/bucket-hub/scripts/github-daily-discover.py`  
   - Runs on **Pi** or **ai-server** with `GITHUB_TOKEN`  
   - Writes `JOBS_DIR/github-daily-candidates.json`  
   - Does **not** clone itself

**Token:** Add `GITHUB_TOKEN` (classic PAT, `public_repo` scope) to `~/homelab/scripts/hub-worker.env`; worker already sends `Authorization` for `api.github.com` in search paths (`hub-worker.py` ~line 912).

### Phase B — Enqueue clones (daily)

Script: `homelab/bucket-hub/scripts/github-daily-enqueue.py`

```text
For each candidate repo html_url:
  Skip if GET /api/jobs finds done job same url in last 7 days (agent logic)
  POST /api/jobs {
    url: https://github.com/owner/repo,
    kind: github,
    drive_id: nas,
    folder_id: github_daily,   ← create folder in library.json or use inbox
    tags: [github, daily-pull, YYYY-MM-DD],
    title: owner/repo
  }
```

Run after discovery; rely on **overnight `queue_drain`** or immediate worker processing.

### Phase C — Schedule

| Option | Mechanism |
|--------|-----------|
| **Pi systemd timer** | `OnCalendar=*-*-* 06:00` Brisbane → `github-daily-enqueue.py` |
| **Overnight step** | New step type `github_daily` in `overnight-schedule.json` calling same script |
| **Maintainer cron** | Mac/Linux cron over Tailscale (least reliable if Mac asleep) |

Prefer **Pi timer** + **wake-ubuntu** already in overnight schedule if clones should run at night.

## Destination layout

Suggest library folder (on TNAS when mounted):

```text
/mnt/tnas/.../github-daily/YYYY-MM-DD/owner-repo/   # optional date shard
```

Requires:

1. Row in `library.json` via Hub storage UI or API merge (`folder_id: github_daily`).
2. If TNAS offline, fail closed or fallback to `ext_movies` / Pi inbox — document in script.

## Dedup and updates

| Strategy | Pros | Cons |
|----------|------|------|
| Skip if job `done` same URL | Simple | No fresh clone on updates |
| `git pull` in worker | Updates | Needs worker change + branch policy |
| Delete dir + re-clone | Clean daily snapshot | Disk churn |

**Maintainer recommendation:** start with **skip-if-cloned-this-week**; add `run_github_job` pull mode later.

## Worker change sketch (optional PR)

```python
# hub-worker.py run_github_job
if Path(target).exists() and (job.get("options") or {}).get("git_update"):
    docker_gluetun([..., "git", "-C", "/repo", "pull", "--ff-only"])
else:
    ... clone ...
```

Expose `git_update` on `JobCreate.options` in `app.py`.

## Verification

```bash
curl -s http://100.85.42.0:3000/api/jobs?tag=daily-pull | jq '.jobs[] | {status, title, url}'
ssh ai-server-ts 'ls -la /mnt/tnas/.../github-daily | head'
```

## Handoff checklist for secondary agent

- [ ] Choose schedule (Pi timer vs overnight step)
- [ ] Implement `github-daily-discover.py` + `github-daily-enqueue.py` under `homelab/bucket-hub/scripts/`
- [ ] Document `GITHUB_TOKEN` in `MANUAL-STEPS.md` (human pastes token)
- [ ] Add library folder `github_daily` procedure
- [ ] Add preset OR pure Search API in discover script
- [ ] Tests: mock GitHub API JSON; dry-run `--enqueue` flag
- [ ] Return diff + deploy commands for maintainer

## Related files

| File | Role |
|------|------|
| `homelab/bucket-hub/hub-worker.py` | `run_github_job`, GitHub search in research/search |
| `homelab/bucket-hub/research-presets.json` | Preset template |
| `homelab/bucket-hub/capture-folders.json` | Default github → nas/inbox |
| `homelab/bucket-hub/app.py` | `POST /api/jobs`, research endpoints |
| `homelab/bucket-hub/scripts/overnight-run.py` | Nightly orchestration |
| `homelab/bucket-hub/overnight-schedule.default.json` | Step templates |
