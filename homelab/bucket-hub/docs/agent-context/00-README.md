# Bucket-HUB — agent context pack

Give this folder (or the linked files below) to a **separate coding agent** that will implement features, automation, and download workflows. The **primary maintainer agent** (Cursor on `llm-swarm-router`) merges recommendations, deploys, and handles secrets.

## Full audit handoff (2026-10)

For a **massive stack audit** (Claude Project or external reviewer), start with **[AUDIT-BRIEF-2026.md](AUDIT-BRIEF-2026.md)** then deep dives **08–14**. Living ops state remains in [PROJECT-STATE.md](PROJECT-STATE.md).

## How to use this pack

1. Read **01 → 07** in order once (or audit brief + 08–14 if auditing).
2. For any change, start from the **canonical git tree** paths in `02-REPOSITORY-AND-DEPLOY-PATHS.md` — not only live server copies.
3. **Never commit** tokens, `hub-worker.env`, `machines.json`, or job JSON from production.
4. **Egress policy:** User-facing search, torrents, GitHub clone, HF, and research run on **Ubuntu ai-server through Gluetun (Proton)**. The Pi Hub API orchestrates; it does not scrape the public internet for those workloads.
5. Return work as: **design note + file list + API contract + test plan + deploy steps** for the maintainer agent.

## Claude Project (PM) vs Cursor (implementer)

- **Setup:** [PROJECT-MANAGER-BRIEF.md](PROJECT-MANAGER-BRIEF.md) — Goal/Context copy-paste, workflow, first message.
- **Living state:** [PROJECT-STATE.md](PROJECT-STATE.md) — Claude emits `## CONTEXT UPDATE`; **Cursor merges** when you paste (see repo `.cursor/rules/homelab-pm-context.mdc`).

## Document index

| File | Contents |
|------|----------|
| [PROJECT-MANAGER-BRIEF.md](PROJECT-MANAGER-BRIEF.md) | Claude Project onboarding |
| [PROJECT-STATE.md](PROJECT-STATE.md) | Living backlog + decisions |
| [01-ARCHITECTURE-AND-NETWORK.md](01-ARCHITECTURE-AND-NETWORK.md) | Hosts, Tailscale/LAN URLs, containers, data flow |
| [02-REPOSITORY-AND-DEPLOY-PATHS.md](02-REPOSITORY-AND-DEPLOY-PATHS.md) | Repo layout, on-disk paths on Pi and ai-server |
| [03-HUB-API-AND-JOB-MODEL.md](03-HUB-API-AND-JOB-MODEL.md) | REST API, job kinds, statuses, worker contract |
| [04-INTEGRATIONS.md](04-INTEGRATIONS.md) | VPN, aria2, Jackett, Sonarr/Radarr, Jellyfin, LLM |
| [05-OPERATIONS-SSH-DEPLOY.md](05-OPERATIONS-SSH-DEPLOY.md) | SSH hosts, deploy scripts, systemd, verification |
| [06-AGENT-WORKFLOWS-DOWNLOADS.md](06-AGENT-WORKFLOWS-DOWNLOADS.md) | Queuing downloads, failures, review queue |
| [07-DAILY-GITHUB-TOP-REPOS.md](07-DAILY-GITHUB-TOP-REPOS.md) | **Start here** for daily top-GitHub automation |
| [AUDIT-BRIEF-2026.md](AUDIT-BRIEF-2026.md) | **Audit entry** — rationale, live snapshot, Claude first message |
| [08-TV-AND-MEDIA-PIPELINE.md](08-TV-AND-MEDIA-PIPELINE.md) | Torrent → Sonarr → Jellyfin |
| [09-WEBSITE-ARCHIVE-SYSTEM.md](09-WEBSITE-ARCHIVE-SYSTEM.md) | httrack / FlareSolverr / wiki batch |
| [10-HUB-UI-AND-COPY.md](10-HUB-UI-AND-COPY.md) | Glance, form.html, pages |
| [11-INTEGRATIONS-AND-EGRESS.md](11-INTEGRATIONS-AND-EGRESS.md) | VPN, Jackett, machines, capture |
| [12-FAILURES-LIMITATIONS-TESTS.md](12-FAILURES-LIMITATIONS-TESTS.md) | Incidents, pytest, smoke, drift |
| [13-NETLLM-CHAT-OVERNIGHT.md](13-NETLLM-CHAT-OVERNIGHT.md) | Chat, overnight, netllm touchpoints |
| [14-SUPERPOWERS-INTENT-VS-SHIPPED.md](14-SUPERPOWERS-INTENT-VS-SHIPPED.md) | Design specs vs code |
| [15-CLAUDE-AUDITOR-WORKFLOW.md](15-CLAUDE-AUDITOR-WORKFLOW.md) | Model, Project limits, overnight + TV folders |
| [SSH-PASTE.txt](SSH-PASTE.txt) | SSH/config + deploy commands (no keys) |
| [../JOB-HEALTH-AND-AGENT-REVIEW.md](../JOB-HEALTH-AND-AGENT-REVIEW.md) | Stuck jobs, `agent_review`, markdown briefs |
| [../MANUAL-STEPS.md](../MANUAL-STEPS.md) | Human-only Cloudflare, passwords, GUI steps |
| [../../TODO.md](../../TODO.md) | Product backlog |

## Related repo (parent)

Bucket-HUB lives inside **`llm-swarm-router`** under `homelab/bucket-hub/`. Parent project **`AGENTS.md`** (repo root) has mesh LLM (`netllm`) docs — orthogonal unless you wire Hub Chat / overnight LLM.

## Public remote git (reference)

Maintainer syncs Hub to **`offline-intelligence/bucket-hub`** (private). Do not assume that remote is checked out on servers; deploy is rsync/scp from maintainer machine or CI.

## Timezone

Schedule and “last night” semantics use **`Australia/Brisbane`** unless overridden in `overnight-schedule.json`.
