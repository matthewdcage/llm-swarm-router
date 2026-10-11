# Claude auditor workflow, model, and limits

Use this with [AUDIT-BRIEF-2026.md](AUDIT-BRIEF-2026.md). You are the **planner / auditor**. Cursor on the Mac implements and deploys.

## Recommended model (cost / outcome)

| Task | Model | Why |
|------|--------|-----|
| **This full-stack audit** | **Sonnet 4.6** (or Sonnet 4.5) | Long context, structured findings, Action Packets. Best cost/outcome for a giant read. |
| Recap / “what did we decide” | Haiku | Cheap; do not use for the audit itself. |
| First implementation slice | **Do not implement in the Project.** If you later use Claude Code on the repo, Sonnet is enough; Opus only if Sonnet’s packet is too shallow. |

**Do not use Opus 5.x for the whole audit.** It will burn tokens on the same documents without a better Action Packet.

## Can Claude become self-sufficient?

**No MCP or skill makes a Claude Project write the Mac repo, SSH, or merge `PROJECT-STATE.md`.** Projects are upload + chat only.

| What you want | What actually works |
|---------------|---------------------|
| Stay in Claude Project | You paste **Action Packets** into Cursor. Cursor merges `## CONTEXT UPDATE`. |
| Claude reads latest code | Attach this GitHub branch (or re-upload files). |
| Claude deploys / SSH | **Cursor** (or Claude Code on the clone) using [SSH-PASTE.txt](SSH-PASTE.txt). Do not put private keys in this Project. |
| “Skill” for Claude | This file + AUDIT-BRIEF. There is no Hub MCP. |

Optional later: **Claude Code** in a clone of this branch, with SSH aliases from `SSH-PASTE.txt`. That duplicates Cursor — only do it if you want one agent both speccing and implementing.

## Required extra audit items (this session)

### Overnight UI (must include)

Current UI is weak. Review and spec a **condensed Hub-chrome** redesign (not a loose Gantt).

- Page: `overnight.html` + Glance last-night card (`hub-overnight.js`, `GET /api/overnight/last-night`)
- Schedule: `overnight-schedule.default.json` (wake TNAS/ubuntu, mount-check, queue-drain, sleep ubuntu)
- Problems to address: Gantt is hard to scan; step status vs jobs is unclear; disabled dsh/seedvault steps still look like first-class UI; no tight “what ran last night / what’s next”
- Output: findings + **one Action Packet** for overnight UI (British English, condensed layout, no status-badge chrome)

### TV folder auto-creation (must include)

Top-down, this may be a mess. Trace and say whether a human can understand `torrents/<Show>/Season NN`.

**Code path**

1. `form.html` `torrentTvDestinationParts` / `torrentTvDestinationRel` — builds `torrents` + show + `Season 01` or `Season 01-09`
2. `ensureLibraryFolderRel` — `POST /api/library/folders` **per path segment** (creates `torrents`, then show, then season)
3. `ensureTorrentShowFolder` — TV chip only; selects Expansion HDD (`ext_movies`)
4. `app.py` `create_folder` — writes `library.json` with `present: true` (worker top-level scan would otherwise miss nested folders)
5. Worker `download_destinations.aria2_dir_for` — host `…/movies/torrents/Show/Season 01` → container `/mnt/torrents/Show/Season 01`
6. **Tension:** worker often uses **flat** aria2 `dir=/mnt/torrents` for torrent jobs so the release folder is not double-nested. Confirm live code in `hub-worker.py` vs Hub library `destination_path`.

**Risks for the auditor**

- library.json accumulates many Hub-created folders; names from release titles can be ugly
- `present: true` without mkdir until claim → enqueue can fail “Folder not found on worker”
- Complete-pack `Season 01-09` vs season-by-season policy
- Sonarr imports to `/data/tv/Show/Season N` — **different tree** from aria2 inbox. Explain this clearly in the Action Packet if you change folders

## Action Packet + CONTEXT UPDATE (still required)

Every reply that changes backlog must end with `## CONTEXT UPDATE` (see PROJECT-STATE.md). Cursor merges it. You cannot skip this.

First implementation packet this session: **highest-risk gap only** (likely TV season-first **or** folder model **or** overnight UI — pick one, list the others as P1).
