# Start here — Bucket-HUB agent brief

## What Bucket-HUB is

A **homelab control plane** for:

- **Download queue** (torrents via aria2 + Proton/Gluetun, YouTube via MeTube/yt-dlp, HTTP, GitHub/HF, website archive).
- **Glance** overview dashboard (machines, VPN, indexers, queue iframe).
- **Storage** treemap inspector, **Chat** (dsh → netllm → vLLM), **Overnight** automation, **Requests** inbox (phone/Access notes).

The **Pi** runs the Hub API and static UI. **Ubuntu ai-server** runs the **worker** and all download/search media docker stacks behind **Gluetun**.

## Non-negotiable constraints

1. **Torrent/search egress via Proton** — fail-closed if VPN down (`hub-worker.py` checks before claim).
2. **Do not commit secrets** — `hub-worker.env`, `machines.json` (live), API keys, Jackett keys in URLs in public docs.
3. **aria2 path contract** — `download_destinations.py` + compose bind `…/torrents:/mnt/torrents` (not `/downloads` overlay). See `docs/HUB-DOWNLOAD-DESTINATIONS.md`.
4. **Hub `/hub-form/*` Caddy route** — required for Glance overview iframe (`?panel=hub`).
5. **Jellyfin Base URL** `/jellyfin` when using Pi proxy; **TV apps** should use **direct ai-server** URL (see `01-architecture-and-hosts.md`).
6. **Leech-only** — `seed-ratio=0`, worker removes aria2 job on complete (files remain for Sonarr). See `docs/ARIA2-THROUGHPUT-AND-LEECH.md`.
7. Prefer **condensed Hub UI** — no gratuitous badges; functional states only.

## Active engineering priorities (Oct 2026)

| Priority | Topic | Notes |
|----------|--------|--------|
| P0 | **Stuck / zombie downloads** | Resume keeps dead `aria2_gid`; aria2 error 13 (partial files, no `.aria2`); metadata-only magnets |
| P0 | **Deploy `job_health` / agent review** | In repo, not necessarily on Pi yet — `job_health.py`, `GET /api/jobs/agent-review` |
| P1 | **Sonarr API from worker** | `SONARR_URL` must work from worker process (Gluetun network: `http://127.0.0.1:8989`); host curl to `:8989` may reset |
| P1 | **Worker ↔ Pi heartbeat timeouts** | Stale VPN badge, slow progress PATCH; worker logs `heartbeat failed: timed out` |
| P2 | **Indexer health** | Jackett indexers 8/30 configured, 4/30 healthy — see Indexers UI |
| Deferred | Seedvault Pi WebDAV → TNAS | `docs/SEEDVAULT.md` |

## Coordination model

- **This agent** may implement code in `homelab/bucket-hub/`, tests, and docs.
- **Primary Cursor agent** (user’s main thread) handles SSH deploy, live incident response, Jellyfin/Sonarr one-offs.
- Return **concrete diffs + deploy checklist**; user or primary agent runs production commands.

## Quick verification (remote, Tailscale)

```bash
# Hub health
curl -s http://100.85.42.0:3000/api/status | jq .

# Jobs
curl -s 'http://100.85.42.0:3000/api/jobs?limit=20' | jq '.jobs[] | {status, title, agent_review}'

# Jellyfin (direct)
curl -sI http://100.112.168.40:8096/jellyfin/web/ | head -3
```

SSH: use **`ai-server-ts`** (Tailscale) when LAN `192.168.50.65` is unreachable; Pi **`stackrise-pi`** or `bucket@100.85.42.0`.
