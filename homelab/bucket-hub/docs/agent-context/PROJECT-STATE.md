# PROJECT-STATE — living context (Homelab integration)

**Maintainers:** Claude PM proposes `## CONTEXT UPDATE`; **Cursor implementer** merges into this file when you paste (see `.cursor/rules/homelab-pm-context.mdc`).  
**Last updated:** 2026-10-12 (AP-1 in progress on cursor/ap-1)

Claude Project: read this file **every session**. Emit patches in `## CONTEXT UPDATE` at end of replies.

---

## Current decisions

- **TV folder model:** Hub stops creating `torrents/<Show>/Season NN`. `torrents/` is the aria2 inbox (`torrents/<release folder>/`); `tv/<Show>/Season N` is the Sonarr-owned library. (Audit 2026-10, C1/C2.)
- **Deploy parity is mandatory:** every packet's deploy runs `scripts/verify-deploy-parity.sh` for Pi and worker, driven by `scripts/deploy-manifest.txt`.
- **Job-kind routing:** a job is aria2 only when `worker == "aria2"`, or no worker and `kind` in {http, magnet}. Shared rule in `job_kinds.py`.
- **Repo archive is segmented from media:** GitHub and HuggingFace downloads get their own job kinds, claim lane and destination roots (spec: `cursor-github-hf-downloader.md`, after AP-1 and AP-2).
- **Testing evidence:** each packet ships `docs/testing/AP-n-test-report.md`.
- **Download index is the top priority:** shared `index_writer.py` (schema v1); every finished job of every kind appends to `$JOBS_DIR/download-index.jsonl` (rich entry for torrents from AP-1, minimal fallback for other kinds until AP-5).
- **Review loop:** Cursor opens a draft PR per packet (`cursor/ap-N`) and posts a "Handoff to PM" comment (also saved to `docs/pm/2026-10/handoffs/AP-N.md`); the PM replies go or no-go before the next packet.

| Topic | Decision |
|-------|----------|
| TV torrents | **Season-by-season** preferred; **discourage** full-series complete packs (size, aria2, Sonarr). Flat `torrents/` inbox. |
| Breaking Bad S01 | Shipped via **season pack** + manual Sonarr scan; Jellyfin **TV Shows (Sonarr)**. |
| Training Day / metadata | Magnet-only dead swarms → use Jackett **`.torrent` URL**; metadata stall = `metadata_stuck` in `job_health.py`. |
| Resume vs Retry | After aria2 restart, **Retry** (clears dead `aria2_gid`); Resume keeps stale gid. |
| Implementer | Cursor on `llm-swarm-router`; deploy Pi `~/homelab/hub-form/`, ai-server `~/homelab/scripts/hub-worker.py`. |
| Claude PM | Specs and Action Packets only; no secrets in docs. |
| Job UI cleanup | **Dismiss** `error`/`done` jobs; `dismiss-queue` clears failed strip; cancel+purge removes disk, not JSON until dismiss. |
| TNAS power | WoL from Pi **best-effort**; hard-off NAS needs physical power or TOS WoL enabled. |
| Website archive | “Website copy” = clone-to-disk system ([09-WEBSITE-ARCHIVE-SYSTEM.md](09-WEBSITE-ARCHIVE-SYSTEM.md)); local Docker preview **not built**. |

---

## Audit handoff (2026-10-12)

- **Master brief:** [AUDIT-BRIEF-2026.md](AUDIT-BRIEF-2026.md) — live probe snapshot, technology rationale, Claude first message.
- **Deep dives:** `08`–`14` in this folder; index in [00-README.md](00-README.md).
- **pytest (compile):** 54 passed, 2 failed — see [12-FAILURES-LIMITATIONS-TESTS.md](12-FAILURES-LIMITATIONS-TESTS.md).
- **Pi deploy parity:** `app.py`, `form.html`, `job_health.py` byte-matched git on 2026-10-12; confirm `hub-worker.py` on ai-server separately.
- **Awaiting Claude auditor:** findings + first Action Packet. Extra required topics: **overnight UI** (`overnight.html`) and **TV folder auto-creation** (`ensureTorrentShowFolder`).
- **Claude is not self-sufficient:** no MCP writes the Mac repo. See [15-CLAUDE-AUDITOR-WORKFLOW.md](15-CLAUDE-AUDITOR-WORKFLOW.md). Cursor still merges CONTEXT UPDATE and SSH-deploys via [SSH-PASTE.txt](SSH-PASTE.txt).
- **Recommended auditor model:** Sonnet 4.6 (not Opus for the full read).

---

## Backlog (ordered)

- [ ] AP-1 (P0) TV flat inbox + deploy parity + XSS sweep (H1) + `_is_aria2_job` fix (L2) + `index_writer.py` on torrent completion. In progress with Cursor.
- [ ] AP-2 (P1) Overnight run-scoped report.
- [ ] Repo archive (P1, after AP-2): AP-3 repo lane + `github_repo`, AP-4 `huggingface_repo`, AP-5 index across all kinds + search, AP-6 recurring runner, AP-7 GitHub stars / HF likes to queue, AP-8 release tracking.

### P0 — TV season-first UX

- [ ] Show-only TV search → Sonarr lookup → **clickable season pips** → Jackett query `{Show} season N}` only.
- [ ] **Hide or demote** `release_kind: full_show` on TV chip (optional “Advanced” override).
- [ ] One season = one queue job; Expansion HDD `torrents/<release>/` (not Show/Season folders).
- [ ] Verify `SONARR_URL=http://127.0.0.1:8989` in `hub-worker.env` (Gluetun network).

### P1 — Automation

- [ ] Daily GitHub top repos: `scripts/github-daily-discover.py` + `github-daily-enqueue.py` (see `07-DAILY-GITHUB-TOP-REPOS.md`).
- [ ] Pi systemd timer (Brisbane); `GITHUB_TOKEN` in worker env (human).

### P2 — Reliability

- [x] Deploy `job_health.py` to Pi with `app.py` (2026-10-12); verify `agent_review` UI end-to-end.
- [ ] aria2 error 13 (partial without `.aria2`): worker policy or user doc for Retry after cleanup.
- [ ] Reduce Pi ↔ worker heartbeat timeouts under load.

### P3 — Later

- [ ] “Missing seasons” batch enqueue (Sonarr episode counts vs queue history).
- [ ] TVMaze fallback if Sonarr lookup fails.

---

## Shipped in repo (not necessarily deployed)

- `docs/agent-context/*` pack
- `job_health.py`, tests, `GET /api/jobs/agent-review`, UI hints (verify deploy)
- `hub_torrent_parse.py` season query filters (deployed earlier for BB S01 search)

---

## Known pain (ops)

- **Office** large complete pack: failed / 0 seeders — prefer per-season.
- **HTTP 400** on worker `PATCH` jobs: Pi API / token / payload; check hub-worker log.
- **TNAS offline**: NAS destinations fail; Expansion HDD is primary for torrents/TV.
- **NAS offline** in Hub UI does not block `ext_movies` torrents.

---

## Session log (newest first)

| Date | Note |
|------|------|
| 2026-10-11 | PM issued Cursor prompts: audit AP-1/AP-2 with test plan and test reports; GitHub + HF repo archive spec (segmented lane, stars-to-queue, searchable index). |
| 2026-10-12 | Audit handoff pack: AUDIT-BRIEF-2026 + docs 08–14; live probes; dismiss UX on Pi. |
| 2026-10-11 | Claude PM workflow defined; PROJECT-STATE created; user uses Tailscale Hub. |

---

## CONTEXT UPDATE template (for Claude PM)

When appending session log or changing backlog, use this shape in replies:

```markdown
## CONTEXT UPDATE

### Last updated
YYYY-MM-DD

### Decisions changed
- (bullet or "none")

### Backlog changes
- [ ] New item / mark [x] done with one-line note

### Session log entry
| YYYY-MM-DD | what happened |
```
