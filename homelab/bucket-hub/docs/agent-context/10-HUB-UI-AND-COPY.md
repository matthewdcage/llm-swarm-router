# Hub UI surfaces and user-facing copy

Audience: audit agent. Primary shell: **Glance** at `:3000` via **Caddy** `hub-gateway`; download UI is **`form.html`** (also `/hub-form/?panel=hub` iframe on overview).

## Route map (Caddy → Pi host :3020)

| Path | Page / asset |
|------|----------------|
| `/` | Glance (`glance.yml`) |
| `/hub-form/*` | `form.html` + API (strip prefix) |
| `/storage` | `storage.html` — treemap (exact path only) |
| `/chat` | `chat.html` |
| `/overnight` | `overnight.html` |
| `/indexers` | `indexers.html` |
| `/capture` | `capture.html` |
| `/api/*` | **Must** proxy to `:3020` — else Glance 404 breaks JSON |

Public: `https://bucket-hub.stackrise.com.au` → Cloudflare Access (email allow-list).

## `form.html` — download manager

| Area | Copy / behaviour |
|------|------------------|
| Modes | Download URL, Search, Ask AI, Archive Site |
| VPN header | Proton badge, egress test, stale heartbeat warning |
| Downloads — live & recent | Mini queue (running, paused, queued, **2 failed**, recent done) |
| Queue sidebar | Active vs History; failed count hint on Hub panel |
| TV / torrent search | Chips: TV, Movies, Games; season bar; provider chips |
| Archive hints | TNAS vs `_nas_staging`; depth 0 = full mirror |
| Errors | `error_display`, humanized aria2 messages, “stopped at N%” |
| Cache | Static JS `max-age=3600` — bump `?v=YYYYMMDD` in `glance.yml` iframe after deploy |

Panels (query `?panel=`): `hub`, `queue`, `sync`, `research`, `minecraft`, `lilly`, `requests`, `machines`, `overview` (default Glance).

## Glance (`glance.yml`)

- Hub overview iframe: `/hub-form/?panel=hub&v=…`
- Widgets: reachability, last-night, machines strip, storage tab link
- **Do not** remove `/hub-form/` route — overview iframe 404s without it

## Static JS modules

| File | Role |
|------|------|
| `hub-vpn.js` | VPN widget, test button |
| `hub-requests.js` | Requests bell modal |
| `hub-overnight.js` | Last-night card |
| `glance-hub.css`, `hub-design.css`, `hub-embed.css` | Chrome |

## Other HTML pages

| File | User-facing purpose |
|------|---------------------|
| `storage.html` | Drive treemap, copy/delete |
| `overnight.html` | Schedule planner |
| `indexers.html` | Jackett health |
| `capture.html` | Capture addon docs / test |
| `chat.html` | LLM on/off + harness iframe |

## UX principles (workspace rules)

- Condensed layouts; avoid loose spacing
- No decorative status badges (REQUIRED, LOCKED) — use disabled controls
- British English in new copy
- Requests: shared inbox, modal approve/dismiss on current page

## Audit questions for Claude

- Consistency of error strings (VPN timeout vs search timeout vs worker 400)
- Whether failed jobs should auto-dismiss after purge
- Season-first TV copy vs still-visible complete-pack results
- Archive preview UX gap (no in-Hub viewer)

## Deploy note

`app.py` serves `form.html` with FileResponse; Pi path `~/homelab/hub-form/`. Byte-size parity with git is a quick drift check (see `AUDIT-BRIEF-2026.md`).
