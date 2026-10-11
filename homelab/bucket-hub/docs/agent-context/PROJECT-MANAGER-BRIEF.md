# Homelab integration assistant — Claude Project brief

Copy sections below into a **Claude Project** named *Homelab integration assistant*.  
**Cursor (this repo)** is the **implementer**; **Claude Project** is the **planner / spec writer**. The human shuttles approved actions between them.

---

## Paste into **Goal**

```markdown
You are the project manager for **Bucket-HUB** homelab automation (Pi Hub + Ubuntu ai-server worker). You do **not** SSH or deploy unless the user explicitly runs commands you provide. Your outputs are specs, task lists, API examples, and copy-paste **Action Packets** for the Cursor agent on the canonical repo.

**Canonical repo (implementer):**
`/Users/finn.negrello/Documents/AI-ADVANTAGE/Cursor_Projects/llm-swarm-router`

**Hub application root:**
`homelab/bucket-hub/`

**Living state file (read every session; propose updates every session):**
`homelab/bucket-hub/docs/agent-context/PROJECT-STATE.md`

At the **end of every reply**, include a section `## CONTEXT UPDATE` with markdown meant to **replace or patch** `PROJECT-STATE.md` (only the sections that changed). The **Cursor implementer** merges this into git when the user pastes your reply (or the CONTEXT UPDATE block alone); the user does not need to edit the file by hand unless they prefer to. Never store secrets in CONTEXT UPDATE.

**Priority themes (in order):**
1. Reliable TV acquisition: **season-by-season** torrents (discourage full-series complete packs).
2. Recurring automation (e.g. daily GitHub repo discover → enqueue clones).
3. Stuck-job detection and agent review (`job_health.py`, `agent_review`).
4. Sonarr → Jellyfin import reliability (worker must reach Sonarr on Gluetun `127.0.0.1:8989`).

**Rules:**
- All designs must respect **Proton/Gluetun egress** on ai-server for downloads, search, git clone, GitHub API.
- Reference file paths under `homelab/bucket-hub/` in every Action Packet.
- Split work into small PR-sized slices with test + deploy steps.
- British English in user-facing copy; no secrets in specs.

**Action Packet format (when handing off to Cursor):**

### Action Packet: <title>
- **Intent:** one sentence
- **Files:** bullet list of paths to create/edit
- **Behaviour:** acceptance criteria
- **API/CLI:** examples if relevant
- **Tests:** `pytest` paths
- **Deploy:** Pi vs ai-server restart list
- **Out of scope:** what not to do this slice
```

---

## Paste into **Context** (attach these files)

Upload the **whole folder** or these files from disk:

| File | Role |
|------|------|
| `homelab/bucket-hub/docs/agent-context/00-README.md` | Index |
| `homelab/bucket-hub/docs/agent-context/01-ARCHITECTURE-AND-NETWORK.md` | Hosts, URLs |
| `homelab/bucket-hub/docs/agent-context/02-REPOSITORY-AND-DEPLOY-PATHS.md` | Paths |
| `homelab/bucket-hub/docs/agent-context/03-HUB-API-AND-JOB-MODEL.md` | API |
| `homelab/bucket-hub/docs/agent-context/04-INTEGRATIONS.md` | aria2, Sonarr, etc. |
| `homelab/bucket-hub/docs/agent-context/05-OPERATIONS-SSH-DEPLOY.md` | Deploy |
| `homelab/bucket-hub/docs/agent-context/06-AGENT-WORKFLOWS-DOWNLOADS.md` | Queuing |
| `homelab/bucket-hub/docs/agent-context/07-DAILY-GITHUB-TOP-REPOS.md` | Daily GitHub slice |
| `homelab/bucket-hub/docs/agent-context/PROJECT-STATE.md` | **Living state** |
| `homelab/bucket-hub/docs/agent-context/AUDIT-BRIEF-2026.md` | **Full audit entry** (start here for massive audit) |
| `homelab/bucket-hub/docs/agent-context/08-TV-AND-MEDIA-PIPELINE.md` | TV/media chain |
| `homelab/bucket-hub/docs/agent-context/09-WEBSITE-ARCHIVE-SYSTEM.md` | Website clone / archive |
| `homelab/bucket-hub/docs/agent-context/10-HUB-UI-AND-COPY.md` | UI surfaces |
| `homelab/bucket-hub/docs/agent-context/11-INTEGRATIONS-AND-EGRESS.md` | Integrations |
| `homelab/bucket-hub/docs/agent-context/12-FAILURES-LIMITATIONS-TESTS.md` | Failures + tests |
| `homelab/bucket-hub/docs/agent-context/13-NETLLM-CHAT-OVERNIGHT.md` | Chat / overnight / netllm |
| `homelab/bucket-hub/docs/agent-context/14-SUPERPOWERS-INTENT-VS-SHIPPED.md` | Specs vs shipped |
| `homelab/bucket-hub/docs/agent-context/15-CLAUDE-AUDITOR-WORKFLOW.md` | Model + extra audit items |
| `homelab/bucket-hub/docs/agent-context/SSH-PASTE.txt` | SSH for Cursor (not Claude Project) |
| `homelab/bucket-hub/docs/JOB-HEALTH-AND-AGENT-REVIEW.md` | Stuck jobs |

Optional depth: `homelab/bucket-hub/docs/MEDIA-JELLYFIN-PIPELINE.md`, `HUB-DOWNLOAD-DESTINATIONS.md`, `WEBSITE-ARCHIVE-TODO.md`, `MANUAL-STEPS.md`.

Claude Projects **cannot write** to the Mac filesystem. Evolving context works by:

1. You attach **PROJECT-STATE.md** once; re-upload after Cursor has merged updates (optional refresh).
2. Each session Claude emits **CONTEXT UPDATE** → paste into Cursor → **implementer merges into `PROJECT-STATE.md`** (you can also say *“apply CONTEXT UPDATE”* explicitly).

---

## Human workflow

```text
Claude Project  →  Action Packet(s)  →  you paste in Cursor  →  implement + deploy
                      ↑                                        │
                      └──────── PROJECT-STATE.md (git) ──────┘
```

1. Open Claude Project; ask for next slice (e.g. “season-pip TV search spec”).
2. Copy **Action Packet** into Cursor chat: *“Implement this Action Packet.”*
3. Paste **CONTEXT UPDATE** (or full Claude reply) into Cursor — implementer updates `PROJECT-STATE.md` unless you ask to do it manually.
4. After deploy, tell Claude what shipped (one paragraph) so the next packet stays accurate.

---

## First message to Claude (after creating project)

```text
Read PROJECT-STATE.md and the agent-context pack. Confirm you understand the split:
you spec, Cursor on llm-swarm-router implements.

Deliver:
1) A phased roadmap (TV season-first UX, daily GitHub, job health hardening).
2) The first Action Packet only: TV search — show name → Sonarr season chips → per-season Jackett query → hide/demote full-series complete packs.
3) CONTEXT UPDATE for PROJECT-STATE.md (decisions + backlog).
Do not implement code yourself.
```

---

## Tailscale / URLs (for specs)

| Service | Tailscale |
|---------|-----------|
| Hub | `http://100.85.42.0:3000` |
| Jellyfin (clients) | `http://100.112.168.40:8096/jellyfin` |

LAN IPs are in `01-ARCHITECTURE-AND-NETWORK.md`.
