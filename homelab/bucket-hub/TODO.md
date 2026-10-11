# Bucket-HUB backlog

Cursor session todos mirror this file. Portable docs: HydraBucket/spill-fork.
Maintainer plans also in llm-swarm-router `docs/superpowers/`.

> **Note (2026-08-25):** Restored after a truncated write. Reconciled against conversation
> transcript + 2026-08-24 `/tmp/TODO-homelab.md` + today's Hub session.

## Changelog (newest first)

| Date | Change |
|------|--------|
| 2026-10-09 | **Process backlog:** Jellyfin lib cleanup (TV Show removed), LVM `/`+`/models`, Chromium-for-Jellyfin, website archive QC (`example.com` httrack) — see § Process improvements (2026-10-09) |
| 2026-10-08 | **Torrent → Jellyfin E2E:** compose + Sonarr/Radarr import path, Hub torrent tags → Jellyfin Tags API, smoke script |
| 2026-10-06 | **Next priority:** Torrent → Jellyfin pipeline (Expansion HDD compose volumes, Jellyfin `/tv`+`/movies`+staging `/torrents`, Sonarr/Radarr import, hub-worker API keys, import *The Office*) — see `docs/MEDIA-JELLYFIN-PIPELINE.md` |
| 2026-09-30 | **Nightly automation + Last Night review:** Overnight schedule now wakes ai-server, drains download queue (poll-until-complete, 2hr budget), sleeps ai-server; auto-wake WoL on high-priority job queue; `GET /api/overnight/last-night` merges run steps + jobs + warnings; "Last Night" card in Hub overview with step timeline, job counts, status badge, warning feed |
| 2026-09-30 | **Cruft audit:** Removed dead `QUEUE_FILE` (queue.jsonl) writes (6 locations), legacy `/hub-form*` Caddyfile route, `/status/*` status-nginx route, `gen-status-pages.py`; consolidated 3 raw job creation paths to `_enqueue_job()`. Created `CHANGES.md` audit trail for all removals with rollback notes |
| 2026-09-30 | **Queue improvements:** Job cancel API (`POST /api/jobs/{id}/cancel`), job retry API (`POST /api/jobs/{id}/retry`), cancel button in Hub UI for running/queued jobs, dev-ops log tail in worker heartbeat + Hub API (`/api/devops/log`) + Worker Log panel in Hub UI, website archive checkpoint/resume (saves crawl state every 10 pages, resumes on retry instead of restarting) |
| 2026-09-30 | **ai-server stability fix:** root cause = USB stale mounts causing D-state cascades. Deployed: worker subprocess-timeout wrappers (`_safe_check_path`, `_safe_disk_usage`, `_safe_listdir`), mount-watchdog.sh systemd timer (60s stale mount detection + lazy unmount), dev-ops.log structured logging |
| 2026-09-30 | **Website archive v3:** URL junk filter (edit/special/template/login pages), file-exists conflict fix (all pages stored as `<path>/index.html`), FlareSolverr crash detection (20 consecutive errors = bail), progress updates every 3 pages |
| 2026-09-28 | **Website archive v2:** FlareSolverr Chrome crawler for Cloudflare-protected sites; inter-page delay reduced 1.5s → 0.3s; Caddyfile fix for Glance internal API (`/api/pages/*` routed to Glance before `/api/*` catch-all) |
| 2026-09-27 | **Website archive infrastructure:** httrack Docker image, FlareSolverr container on Gluetun, `_run_flaresolverr_crawl()` in worker, sitemap discovery, Hub queue archive progress UI, soyjakwiki.org first target |
| 2026-08-26 | Uptime Kuma: admin + 10 LAN monitors via bootstrap script on Pi |
| 2026-08-26 | Jackett: set admin password + rotated API key (screenshot leak); Hub worker env updated; Torznab OK |
| 2026-08-26 | LAN smoke test: Hub/download APIs OK; MeTube yt-dlp via Proton failing again — cookies re-export deferred |
| 2026-08-26 | MeTube: refreshed Netscape YouTube cookies on Ubuntu; yt-dlp zoo probe OK via Proton (later regressed — see active item) |
| 2026-08-26 | AriaNg: verified aria2 RPC via Ubuntu SSH; `scripts/ariang-wire-rpc.sh` one-shot LAN setup URL (secret stays on server) |
| 2026-08-26 | Manual LAN frontend check done (screenshots); follow-ups: AriaNg RPC via Ubuntu SSH, MeTube cookies, Jackett password, Uptime Kuma setup, Mac netllm |
| 2026-08-25 | Seedvault design locked (Pi WebDAV → TNAS; nightly ordered ops); done for night |
| 2026-08-25 | Seedvault design intent: Pi WebDAV → TNAS path; nightly incremental; merge overnight TODO |
| 2026-08-25 | Glance tab **Storage Overview** → `/storage`; clarify Seedvault vs Hub queue |
| 2026-08-25 | Defer GrapheneOS Seedvault backups; next: Seedvault → TNAS overnight |
| 2026-08-25 | Storage done (gp-treemap); next: GrapheneOS Seedvault backups + 4-day alert |
| 2026-08-25 | Storage visual inspector: `/storage` + header button, treemap, copy/delete |
| 2026-08-25 | TODO: manual LAN frontend login checks; start Storage visual inspector |
| 2026-08-25 | LAN services audit (AriaNg/MeTube/Jellyfin/Uptime/TNAS/netllm); Glance Jackett+SearXNG |
| 2026-08-25 | YouTube on Proton: MeTube Deno + cookies path; Hub YT cookies status |
| 2026-08-25 | Research/Upcoming R1 (HN/GitHub/HF presets via Proton) + human job errors |
| 2026-08-25 | Phase D3: Access phone queuer + shared Requests badge on Hub |
| 2026-08-25 | Phase D2: Jackett torrent search (magnets via Proton; chip + queue) |
| 2026-08-25 | Phase D1: SearXNG web/YouTube search + provider chips |
| 2026-08-25 | VPN badge in Glance header; Queue Active/History redesign |
| 2026-08-25 | Queue polish (filters, detail, Lilly badge) + VPN live indicator |
| 2026-08-25 | Queue panel on Glance; capture file-type folders; Firefox addon 1.0.7; TODO restore |
| 2026-08-25 | Phase A/B/C downloader: Proton search, preview, Firefox capture |
| 2026-08-25 | Machines wake/sleep; overnight card restyle |
| 2026-08-24 | Cloudflare Hub docs / Access notes |
| 2026-08-22 | Jellyfin libraries; aria2/MeTube dirs; worker v2; TNAS mount |

## Next priority — Torrent → Jellyfin E2E

- [ ] **A1–A6** Run on LAN: [`scripts/deploy-media-stack-ai-server.sh`](scripts/deploy-media-stack-ai-server.sh) (dirs, aria2 exFAT, compose, worker restart)
- [ ] **A3** Jellyfin UI: Shows → `/tv`, Movies → `/movies` (drop misleading `media-fallback` libs) — [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) §8
- [ ] **A4** Sonarr `/data/tv` + Radarr `/data/movies`; aria2 download client + remote path `/downloads`
- [ ] **A5** `hub-worker.env`: `JELLYFIN_*`, `SONARR_*`, `RADARR_*` (secrets not in git)
- [ ] **A7** Import *The Office* from `torrents/` via Sonarr manual import → Jellyfin scan
- [x] **B** `scripts/hub-torrent-jellyfin-smoke.sh` + QC doc section
- [x] **C** Hub torrent search tags on enqueue; `.hub-job.json` sidecar; Jellyfin Tags API after import

Guide: [`docs/MEDIA-JELLYFIN-PIPELINE.md`](docs/MEDIA-JELLYFIN-PIPELINE.md).

Website archive improvements: [`docs/WEBSITE-ARCHIVE-TODO.md`](docs/WEBSITE-ARCHIVE-TODO.md).

## Done (recent)

- [x] Glance Hub UI + hub-form + worker API
- [x] Ubuntu hub-worker (HTTP/magnet/video/GitHub/HF via Gluetun; fail-closed if VPN down)
- [x] Media + TNAS mounts; NAS destination E2E
- [x] Dynamic drive discovery (worker -> Hub library)
- [x] Notify when done (Telegram)
- [x] Night drain timer disabled; downloads via Hub queue + Ubuntu worker
- [x] Cloudflare Tunnel + Access for remote Hub (bucket-hub.stackrise.com.au)
- [x] Form status banner (worker / VPN / media / NAS chips)
- [x] Downloader Phase A/B (modes, Proton search, preview)
- [x] Firefox capture addon + Hub capture API (Proton enqueue)
- [x] File type -> folder map (YouTube -> TNAS media-archive; Hub-owned `capture-folders.json`)
- [x] Glance **Queue** card (`?panel=queue`) Active + Recent jobs
- [x] Machines wake/sleep (WoL + SSH suspend; Pi status-only; TNAS wake-only v1)
- [x] Minecraft Java (Fabric) on ai-server + Hub controls

## Active — Hub polish

- [x] **Hub overview Queue card polish** - Active/History compact rows; Lilly review badge; detail on click
- [x] **VPN live indicator** - centered Glance header badge (~5s); Gluetun kill switch + Hub fail-closed
- [x] **Phase D1 providers** - SearXNG web + YouTube search; provider registry/chips
- [x] **Phase D2 Jackett** - torrent chip; magnets via Proton; indexers in Jackett UI; no API-key leak in Hub results
- [x] **Phase D3** - Access phone queuer (`/hub-form/capture`); shared Requests + Hub badge; Hub is primary UI
- [x] **Research/Upcoming R1** - Hub presets (AI/ML, HN, unrestricted) via Proton; Queue/Request actions
- [x] **Human-readable errors** - `error_display` on jobs; mount / yt-dlp / aria2 hints
- [x] **YouTube on Proton** - MeTube image (Deno) + `data/ytdlp/cookies.txt`; Hub status chip; clear error if missing
- [x] **LAN services audit** - reachability table; CF tunnel not proxying media UIs; Glance + Jackett/SearXNG; netllm Mac down
- [x] **LAN services — manual Hub frontend check** - Screenshots 2026-08-26 from home LAN; findings → follow-ups below
- [x] **Storage visual inspector** - `/storage` + Glance **Storage Overview** tab; **gp-treemap**; Media/USB/TNAS; copy + confirm-delete (header Storage icon removed)
- [x] **Hub header polish** - Theme picker hidden; Requests bell opens review-only modal (Approve/Dismiss); larger modal type

### Active — LAN frontend follow-ups (from 2026-08-26 check)

- [x] **AriaNg RPC auth** - Confirmed aria2 `:6800` accepts `ARIA2_RPC_SECRET` (SSH Ubuntu / Tailscale). Added `~/homelab/scripts/ariang-wire-rpc.sh` (writes `data/ariang-setup.url` mode 600; AriaNg hash uses base64url secret). Opened one-shot setup URL on Mac browser — refresh AriaNg; **Aria2 Status** should show Connected. Re-run: `ssh ai-server '~/homelab/scripts/ariang-wire-rpc.sh'` then open that URL once if settings cleared.
- [x] **MeTube YouTube cookies (first pass)** - Installed Netscape export Aug 26; worked briefly then regressed on Proton smoke test
- [ ] **MeTube YouTube cookies — re-export (deferred)** — see [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) §4
- [x] **Jackett harden** - Admin password set; API key rotated. Creds → password manager — [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) §5
- [x] **Uptime Kuma first setup** - Admin + 10 monitors. Creds → password manager — [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) §5
- [ ] **netllm Mac** _(low priority)_ — [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) §6
- [ ] **netllm Ubuntu Doctor** _(optional, skip)_ - Dashboard Doctor banner from remote LAN is expected without `swarm.cluster_token`. No action unless you want LAN admin UI.
- [x] **Jellyfin / SearXNG / TNAS** - UIs load (login / search / TOS). No blocking action; spot-check libraries when convenient.

| 2026-08-26 | Chat `/chat` + LLM on/off; DeepSeek Harness on Ubuntu; overnight planner `/overnight`; on-demand vLLM (Qwen3.8) |
| 2026-08-26 | Hub design language v2: single workspace iframe, hub-design.css tokens |
| 2026-08-26 | Hub overview usability pass + [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md) for human-only ops |

### Done (2026-08-26 — Chat + overnight)

- [x] **Hub Chat** — `/chat` wrapper, LLM toggle, iframe → `chat.bucket-hub.stackrise.com.au` (tunnel route manual)
- [x] **On-demand vLLM** — `ai-server-llm.sh`, vLLM off by default; Qwen3.8-27B compose (`~/homelab/vllm-compose.yml`)
- [x] **DeepSeek Harness** — dsh web `:3080` on Ubuntu (Node 22.19 user-local)
- [x] **Overnight planner v2** — Gantt night-window view, `complete_or_timeout` advance mode, live run status API
- [x] **Hub overview usability** — dispatcher embed, nav order, ops moved to Dev; spec `docs/superpowers/specs/2026-08-26-hub-overview-usability-design.md`
- [x] **Hub design language v2** — single `?panel=hub` workspace; `hub-design.css`; Copy/sync + Research folded; Reachability on Dev
- [x] **Hub a11y v2.1** — contrast/labels/Mounts strip; Overnight text pills; Queue column visible + widened; deployed + `hub-overview.png`
- [x] **Hub queue-first rescue** — 2-col hero+Active queue; Overnight/Machines folded; History → full queue; deployed

### Manual (you) — see [`docs/MANUAL-STEPS.md`](docs/MANUAL-STEPS.md)

- [ ] **Cloudflare Chat tunnel** — `chat.bucket-hub.stackrise.com.au` → Ubuntu `:3080`
- [ ] **Qwen3.8 weights pull** — `ai-server-llm.sh pull` then Chat smoke
- [ ] **Deploy overnight v2 / Hub UI** — SCP + restart (if not already applied from your Mac)
- [ ] **MeTube cookies re-export** — Proton + Current Site → Ubuntu cookies.txt
- [ ] **Password manager** — Jackett / Uptime Kuma one-shot cred files → vault, delete
- [ ] **netllm Mac** _(low)_ — LAN listen when needed

### Active — ai-server stability (2026-09-30)

- [x] **Root cause investigation** — USB drives intermittently disconnect (kernel `error -110`); stale mounts cause D-state cascades that brick SSH/systemd. TNAS CIFS mount is NOT the cause. Memory pressure not a factor (21/62GB used).
- [x] **Worker safe stat wrappers** — `_safe_check_path()`, `_safe_disk_usage()`, `_safe_listdir()` use subprocess with timeout instead of in-process `os.stat()` / `shutil.disk_usage()`. Stale mount paths tracked in `_STALE_MOUNTS` set, auto-recovered when drive unmounts.
- [x] **Mount watchdog** — `scripts/mount-watchdog.sh` + systemd timer (every 60s). Detects stale `/mnt/external/*` mounts, force-unmounts with `umount -l`. Logs to `dev-ops.log`.
- [x] **Dev ops log** — Structured log at `/home/bucket/homelab/logs/dev-ops.log`. Sources: watchdog, worker. Format: `[timestamp] [source] [level] message`.
- [ ] **Deploy & verify** — SCP worker + watchdog to ai-server, enable timer, re-queue soyjakwiki crawl, verify watchdog catches simulated stale mount.

### Active — Nightly automation (2026-09-30)

- [x] **Overnight schedule v2** — Wake TNAS + ai-server, drain queue (poll-until-complete, 120m budget), sleep ai-server. Research/log review steps kept disabled (low priority).
- [x] **Auto-wake on high-priority** — `_auto_wake_worker_if_needed()` sends WoL to ai-server when high-priority job is queued and heartbeat is >2min stale.
- [x] **Queue drain enhanced** — `overnight-run.py` queue_drain step polls `/api/jobs?limit=50` every 15s until zero active jobs or timeout; logs progress every 60s.
- [x] **TNAS sleep probe** — SSH disabled (port 22 refused). TOS web admin on :8181. No documented shutdown API. TNAS stays wake-only. To add sleep: enable SSH in TOS admin panel.
- [ ] **Deploy overnight + enable timer** — SCP overnight-run.py to Pi, enable `overnight-run.timer` systemd timer.

### Active — Cruft audit (2026-09-30)

- [x] **QUEUE_FILE removed** — Append-only `queue.jsonl` was written but never read; `claim_job()` reads from `JOBS_SUB.glob()`. Removed from 6 locations + `_ensure_dirs`.
- [x] **Legacy routes removed** — `/hub-form*` prefix-stripping route (from pre-Glance era) and `/status/*` status-nginx route (replaced by `/api/overnight/status`).
- [x] **gen-status-pages.py removed** — Generated static HTML for now-dead status-nginx container.
- [x] **Enqueue consolidated** — 3 endpoints (sync, storage_delete, storage_copy) now use `_enqueue_job()` instead of raw file writes.
- [x] **CHANGES.md created** — Audit trail with datetime, what, why, files, rollback for every removal.

### Active — Last Night review (2026-09-30)

- [x] **`/api/overnight/last-night` endpoint** — Merges overnight run JSONL (step results) + prior day job summary + machine events + dev-ops warnings.
- [x] **Last Night card** — Hub overview: step timeline (green/red dots), job counts + top 3, status badge (OK/FAILED/idle), warnings feed.
- [ ] **Deploy & verify** — SCP app.py + form.html to Pi, verify card renders with overnight data.

### Active — Website archive

- [x] **FlareSolverr crawler** — Chrome-based crawl for Cloudflare-protected sites; auto-selected when probe returns 403
- [x] **URL junk filter** — Skip wiki edit/login/special/template pages; reduced soyjakwiki queue from 29k to realistic page count
- [x] **File conflict fix** — All pages stored as `<path>/index.html`; handles URL paths that are both pages and directory prefixes
- [x] **FlareSolverr crash detection** — 20 consecutive errors = bail with error message instead of silently burning through queue
- [ ] **soyjakwiki.org full site** — Re-queue after stability fix deployment; previous runs: v1 132 pages, v2 351 pages, v3 687 pages (all hit bugs now fixed)

### Active — Hub / ops next

- [ ] **GrapheneOS Seedvault → TNAS (deferred, design locked)** - Pi WebDAV → TNAS `backups/pixel9/`; nightly ordered ops while machines awake; 4-day staleness alert when built. Spec: `docs/superpowers/specs/2026-08-25-hub-seedvault-tnas-design.md`
- [ ] Rotate Proton WG key (never commit)
- [ ] spill-fork sync (worker, mount scripts, spill-explanation.md)
- [ ] **Stream Deck Mini** - one real action per key (swarm, nightly sync, Blender prep)

## Later (after current Hub downloader / ops items)

- [ ] **Scheduled deep research (last30days)** - v1 overnight runner triggers preset research; full last30days binary + LLM synthesis via dsh headless still Later
- [ ] **LLM log review** - v1 writes last-night summary; full dsh log synthesis Later

## P3 - watchlist

- [ ] DVD burner infrastructure (burn station, queue, Bucket-HUB hook)
- [ ] 4DX announcement scanning (ASX / 4DX.AX)
- [ ] Torrent/magnet peer health through Proton (partial smoke only)

## Review cadence

- After each deploy: add a Changelog row; move items Done <-> Active
- Weekly: glance at error jobs in form + Queue card
