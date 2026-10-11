# Bucket-HUB — agent handoff pack

Use this folder as **primary context** for a separate coding agent (Claude, Cursor, etc.) working on Bucket-HUB without the full `llm-swarm-router` conversation history.

## How to use

1. Read **`00-START-HERE.md`** first (scope, goals, coordination with the main agent).
2. Read **`01-architecture-and-hosts.md`** (machines, URLs, network).
3. Read **`02-repository-and-paths.md`** (where code lives on Mac, Pi, Ubuntu).
4. Deep-dive by topic (`03`–`08`) as needed for your task.
5. Cross-check **`09-existing-docs-index.md`** for maintained runbooks (do not duplicate long procedures — link instead).

## Deliverables back to the primary agent

When you finish work, return:

- **Summary** of changes (files + behaviour).
- **Deploy steps** (Pi vs ai-server, restart commands).
- **Verification** commands (curl/smoke scripts).
- **Risks / follow-ups** (especially aria2 paths, Sonarr API via Gluetun, secrets).

## Canonical source tree (Mac / git)

| Location | Role |
|----------|------|
| `llm-swarm-router/homelab/bucket-hub/` | **Canonical** Hub app, worker, docs, tests (this repo) |
| `llm-swarm-router/.tmp-hub-ui/` | Legacy/staging copy — prefer `homelab/bucket-hub/` |
| GitHub `offline-intelligence/bucket-hub` | Private deploy remote (do not commit `machines.json` secrets) |

## Live deploy targets (not in git)

| Host | Path | Service |
|------|------|---------|
| Pi (`stackrise-pi`) | `~/homelab/hub-form/` | `hub-form.service` (FastAPI `:3020`) |
| Pi | `~/homelab/glance/` | Glance + Caddy `hub-gateway` `:3000` |
| Pi | `$JOBS_DIR` (default `/data/hub-jobs`) | Job JSON, library, agent-review briefs |
| ai-server | `~/homelab/scripts/hub-worker.py` | `hub-worker.service` |
| ai-server | `~/homelab/scripts/hub-worker.env` | Secrets (never commit) |
| ai-server | `~/homelab/docker-compose.yml` | Gluetun, aria2, Sonarr, Radarr, Jellyfin, … |

## Document map

| File | Contents |
|------|----------|
| [00-START-HERE.md](00-START-HERE.md) | Mission, constraints, active priorities |
| [01-architecture-and-hosts.md](01-architecture-and-hosts.md) | Topology, Tailscale/LAN URLs |
| [02-repository-and-paths.md](02-repository-and-paths.md) | File-level map of the codebase |
| [03-services-integrations.md](03-services-integrations.md) | Jackett, SearXNG, VPN, LLM, Glance |
| [04-download-pipeline.md](04-download-pipeline.md) | Jobs, aria2, worker, concurrency |
| [05-media-jellyfin-sonarr.md](05-media-jellyfin-sonarr.md) | Import pipeline, playback |
| [06-api-and-data-model.md](06-api-and-data-model.md) | Hub API, job schema, statuses |
| [07-deploy-operations-ssh.md](07-deploy-operations-ssh.md) | SCP, systemd, docker, smoke tests |
| [08-known-issues-backlog.md](08-known-issues-backlog.md) | Stuck jobs, resume/gid, agent-review |
| [09-existing-docs-index.md](09-existing-docs-index.md) | Pointers to other `docs/*.md` |

Updated: 2026-10-11
