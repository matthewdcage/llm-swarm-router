# Website archive — improvement backlog

Process: full-site mirrors via Hub **Archive Site** → Ubuntu `hub-worker` → **Gluetun/Proton** → **httrack** / **FlareSolverr** / **MediaWiki API**.  
Canonical destination: **TNAS → Website Backups** (`/mnt/tnas/Website Backups/<domain>/`).  
Manual ops: [`MANUAL-STEPS.md`](MANUAL-STEPS.md). Queue UI: `form.html` **Archive Site** mode.

---

## P0 — Reliability and destination

- [x] **TNAS mount gate** — Warn before enqueue when `nas` is unmounted; auto **Expansion HDD → `_nas_staging`** fallback with “move to TNAS later” copy (Hub UI 2026-10-09).
- [x] **Pre-flight probe in UI** — `POST /api/archive/probe` + debounced hint in Archive Site mode (2026-10-09).
- [ ] **Wake TNAS from Hub** — Machines strip WoL for NAS before archive when destination is TNAS (today: manual wake).
- [ ] **Retry/resume visibility** — Surface `.crawl-checkpoint.json` / httrack `hts-cache` in queue detail (“Resume available”).
- [ ] **VPN fail-closed** — If Gluetun unhealthy, reject new `website_archive` jobs with actionable error (verify worker already skips sensitive jobs).

## P1 — Progress and observability

- [ ] **httrack progress** — Parse `hts-log.txt` or periodic `find | wc` so queue shows activity (today: page counts mainly on FlareSolverr/MediaWiki).
- [ ] **Engine + phase in list row** — Mini queue strip: `httrack · crawling` / `FlareSolverr · 142 pages` without opening detail.
- [x] **Post-complete stats** — Job `progress`: files count + bytes after crawl (worker 2026-10-09); `.hub-job.json` sidecar on success.
- [ ] **Dev-ops log link** — One-click tail hint for `journalctl -u hub-worker` during long crawls.

## P2 — UX and defaults

- [x] **Archive destination auto-select** — Prefer TNAS Website Backups; fallback `_nas_staging` when NAS offline (Hub UI).
- [x] **Depth copy** — Clarify `0` = full mirror (`-r99`), not “single page” (Hub UI 2026-10-09).
- [ ] **Rate / time estimate** — `~500 KB/s` cap → rough ETA for large sites.
- [ ] **Domain subfolder preview** — Show final path `…/Website Backups/example.com` before queue.
- [ ] **Storage tab link** — After done, deep-link treemap to new archive folder.

## P3 — Quality and safety

- [ ] **Robots.txt toggle** — Today `--robots=0`; optional respect robots for polite crawls.
- [ ] **Allowlist / max pages** — Safety cap for runaway crawls (per job option).
- [ ] **Exclude patterns** — Skip login, cart, `?replytocom=` junk (FlareSolverr path has partial filters).
- [ ] **Deduplicate concurrent archives** — Same URL + destination → queue or merge warning.

## P4 — Integrations

- [ ] **Scheduled re-archive** — Overnight slot after TNAS wake (tie to `overnight-run.py`).
- [ ] **SearXNG / capture** — “Archive this result” from search row.
- [ ] **Viewer** — Open static mirror (local `file://` or simple Caddy browse of TNAS path); see `scripts/soyjak-archive-viewer.py`.
- [ ] **Document Jellyfin vs archive** — No overlap; archives live under Website Backups only.

## Done / verified

- [x] End-to-end **httrack** job (`example.com`, depth 2) → `_nas_staging` (2026-10-09).
- [x] **FlareSolverr** + **hubhttrack** images on ai-server; health OK.
- [x] Hub **Archive Site** mode, depth selector, queue detail engine line (FlareSolverr/httrack).
- [x] MediaWiki API batch path in worker (soyjakwiki reference).

---

## Test checklist (after changes)

1. Hub → **Archive Site** → URL `https://example.com` → depth **2** → `_nas_staging` or TNAS → **Start archive**.
2. Queue: status **Archive**, engine **httrack**, completes **done**.
3. Worker host: files under `destination_path/<domain>/`.
4. TNAS offline: UI warning + staging fallback (no silent fail on missing folder).
5. (Optional) CF site: engine **FlareSolverr**; wiki: **mediawiki-api**.

Updated: 2026-10-09
