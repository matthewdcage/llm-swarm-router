# Bucket-HUB audit brief (2026-10-12)

**Purpose:** Single entry point for the Claude Homelab integration assistant (or any auditor) to understand Bucket-HUB without SSH. Pair with deep dives **08–14** in this folder and living [`PROJECT-STATE.md`](PROJECT-STATE.md).

**Canonical code:** `llm-swarm-router/homelab/bucket-hub/`  
**Deploy:** Pi `~/homelab/hub-form/` + `~/homelab/data/hub-jobs/`; ai-server `~/homelab/scripts/hub-worker.py` + `~/homelab/docker-compose.yml`

---

## 1. Executive map

```text
[Browser] → Caddy hub-gateway :3000 (Pi)
              ├─ Glance (overview, widgets)
              ├─ /hub-form → form.html + static JS
              └─ /api/* → app.py :3020 (FastAPI, job JSON store)

[hub-worker on ai-server] ←X-Worker-Token→ Pi API
              └─ Docker via Gluetun (Proton)
                    ├─ aria2, Jackett, Sonarr, Radarr, Jellyfin
                    ├─ FlareSolverr, httrack, MeTube, …
                    └─ mounts: /mnt/external/movies, /mnt/tnas (optional)
```

| URL (examples) | Use |
|----------------|-----|
| `http://100.85.42.0:3000` | Hub (Tailscale Pi) |
| `https://bucket-hub.stackrise.com.au` | Public (Cloudflare Access) |
| `http://100.112.168.40:8096/jellyfin` | Jellyfin (Tailscale ai-server) |

---

## 2. Technology rationale (why these choices)

| Choice | Why |
|--------|-----|
| Pi orchestrates, ai-server executes | Pi always on; GPU/USB media on ubuntu; keeps torrent load off Pi SD |
| Gluetun + Proton | Single egress policy; fail-closed for sensitive jobs |
| aria2 not qBittorrent | RPC fit for worker supervisor; slot/Mbps gating in Python |
| Jackett not per-indexer wiring | One Torznab surface; Hub chips map to categories |
| Expansion HDD exFAT | Large cheap volume; `file-allocation=none` |
| Leech-only | Homelab policy; files remain for Sonarr import |
| Sonarr/Radarr before Jellyfin library | Named seasons/episodes; torrent folder is staging |
| Season-by-season TV (direction) | Complete packs fail often (Office, BB REMUX) |
| httrack + FlareSolverr + MediaWiki API | Layered archive engines by site type |
| Job JSON on Pi | Simple queue; no Redis; worker polls claim/PATCH |
| Firefox for Jellyfin | H.264 transcode policy; avoid Chrome tracking |

---

## 3. Assumptions register

- Trusted home LAN; worker token shared Pi ↔ ubuntu only
- `machines.json` and `hub-worker.env` maintained on servers, not in git
- TNAS may be offline; USB media path must work without NAS
- WoL wakes **sleeping** NICs; **hard-off TNAS** needs physical power or BIOS WoL enabled
- Deploy is rsync/scp — **git on Mac can match Pi** while **worker copy lags**
- Australia/Brisbane for overnight and “last night”
- Parent `llm-swarm-router` netllm mesh is separate unless Chat/overnight LLM enabled

---

## 4. Known wins

- Breaking Bad S01 season pack → Sonarr → Jellyfin (7 episodes)
- MediaWiki API batch archive path (soyjakwiki-scale reference)
- Bandwidth-gated multi-torrent aria2 (`HUB_ARIA2_TARGET_MBPS`)
- Job dismiss + bulk `dismiss-queue` clears UI clutter
- Caddy routes `/api/*` to Hub backend (Glance 404 fix)
- `job_health.py` + agent review API (in repo; verify worker deploy)
- Overnight wake ubuntu + queue drain + sleep

---

## 5. Known failures / incidents

See [`12-FAILURES-LIMITATIONS-TESTS.md`](12-FAILURES-LIMITATIONS-TESTS.md). Headlines: complete-series torrents, stale aria2 gids, Sonarr URL inside Gluetun, TNAS WoL miss when powered off, partial purge vs job records, hub-form import crash if `job_health.py` missing.

---

## 6. Gaps in maintainer knowledge

- Live Jackett indexer health vs UI “8/30” snapshot
- MeTube cookie validity at audit time
- TerraMaster WoL enabled or not in firmware
- ai-server `hub-worker.py` byte parity with git (not checked 2026-10-12)
- Radarr/Jellyfin movies path less battle-tested than TV

---

## 7. Audit domains index

| Doc | Topic |
|-----|--------|
| [08-TV-AND-MEDIA-PIPELINE.md](08-TV-AND-MEDIA-PIPELINE.md) | Torrent → aria2 → Sonarr → Jellyfin |
| [09-WEBSITE-ARCHIVE-SYSTEM.md](09-WEBSITE-ARCHIVE-SYSTEM.md) | Site clone engines + future local viewer |
| [10-HUB-UI-AND-COPY.md](10-HUB-UI-AND-COPY.md) | Glance, form.html, pages |
| [11-INTEGRATIONS-AND-EGRESS.md](11-INTEGRATIONS-AND-EGRESS.md) | VPN, Jackett, machines, capture |
| [12-FAILURES-LIMITATIONS-TESTS.md](12-FAILURES-LIMITATIONS-TESTS.md) | Tests + smoke + drift |
| [13-NETLLM-CHAT-OVERNIGHT.md](13-NETLLM-CHAT-OVERNIGHT.md) | Chat, overnight, netllm touchpoints |
| [14-SUPERPOWERS-INTENT-VS-SHIPPED.md](14-SUPERPOWERS-INTENT-VS-SHIPPED.md) | Design history gaps |
| [01–07](00-README.md) | Baseline architecture, API, ops |
| [../agent-handoff/](../agent-handoff/) | Alternate handoff copies |

**Out of runtime path:** `.cursor/coordinator/` (gitignored maintainer outreach tooling).

---

## 8. Recommended audit order

1. Read this brief + `PROJECT-STATE.md`
2. [08] TV pipeline — highest user pain
3. [12] Failures + test matrix — reproduce priorities
4. [09] Website archive — product scope for Docker preview
5. [11] Egress + machines + TNAS
6. [10] UI/copy consistency
7. [14] Superpowers gaps vs backlog
8. [13] Overnight + Chat + netllm boundaries
9. Produce findings by severity + one Action Packet for top risk

---

## 9. Live verification snapshot

**Compiled:** 2026-10-12 (Australia/Brisbane). Maintainer off-LAN; probes via Tailscale + ai-server SSH.

| Probe | Result | Notes |
|-------|--------|-------|
| Pi `hub-form` systemd | **pass** | active |
| `GET :3020/api/jobs` (Pi loopback) | **pass** | HTTP 200 |
| `GET :3000/` Glance gateway | **pass** | HTTP 200 |
| `GET :3020` via Tailscale `100.85.42.0` | **pass** | HTTP 200 |
| Hub from ai-server LAN | **pass** | HTTP 200 |
| `GET /api/machines` | **pass** | pi/ubuntu up; mac/bucket/tnas down |
| `GET /api/status` | **pass** | worker, vpn fields present |
| ai-server `hub-worker` | **pass** | active |
| aria2 RPC getVersion | **pass** | |
| Sonarr :8989 in container | **pass** | HTTP 200 |
| Jellyfin health | **pass** | HTTP 200 |
| TNAS ping `192.168.50.186` | **fail** | powered off or unreachable |
| `/mnt/tnas` mount on ai-server | **fail** | not mounted |
| TNAS WoL from Pi | **inconclusive** | packet sent; no ping after ~2.5 min |
| pytest `PYTHONPATH=. tests/` | **partial** | 54 pass, 2 fail (see 12) |
| hub-lan-smoke / torrent-jellyfin smoke | **not run** | requires LAN runner |
| Pi vs git `app.py`/`form.html`/`job_health.py` size | **pass** | identical byte counts |

---

## 10. Claude auditor — first message

Use the **updated** paste in the user-facing handoff (or [15-CLAUDE-AUDITOR-WORKFLOW.md](15-CLAUDE-AUDITOR-WORKFLOW.md)). Must cover overnight UI + TV folder auto-creation. Model: **Sonnet 4.6**.

---

## 11. Upload list for Claude Project

Attach entire `homelab/bucket-hub/docs/agent-context/` folder, or minimum:

- `AUDIT-BRIEF-2026.md`
- `PROJECT-STATE.md`
- `PROJECT-MANAGER-BRIEF.md`
- `00-README.md` through `07-*.md`
- `08` through `14`
- Optional: `MEDIA-JELLYFIN-PIPELINE.md`, `WEBSITE-ARCHIVE-TODO.md`, `JOB-HEALTH-AND-AGENT-REVIEW.md`

Do **not** upload `hub-worker.env`, `machines.json`, or job JSON from production.
