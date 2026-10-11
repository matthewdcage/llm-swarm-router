# Superpowers specs — intent vs shipped

Historical design lives in `llm-swarm-router/docs/superpowers/{specs,plans}/`. Canonical implementation: `homelab/bucket-hub/`. This table is for audit gap analysis — not every row is P0.

| Spec / plan | Topic | Shipped (summary) | Gap |
|-------------|-------|-------------------|-----|
| `hub-downloader-requirements.md` | Proton egress, queue | Yes — Gluetun worker, VPN badge | MeTube cookies intermittent |
| `hub-downloader-search-capture-design.md` | Search + capture phases | SearXNG, Jackett, Firefox addon | Capture polish |
| `hub-phase-d1-searxng-providers-design.md` | Provider chips | `search-providers.json`, UI chips | Provider health UX |
| `hub-phase-d2-jackett-torrent-design.md` | Torrent search | TV/Movies/Games chips, aria2 | Season-first UX incomplete |
| `hub-phase-d3-access-capture-design.md` | Phone queuer, Requests | Requests modal, Lilly paths | Deprecate standalone form |
| `hub-vpn-header-queue-redesign-design.md` | VPN center, queue | Header VPN, queue column | History on Hub panel |
| `hub-queue-first-rescue-design.md` | Queue-first overview | Live & recent strip, reorder | Failed row dismiss UX (partial fix 2026-10) |
| `hub-overview-usability-design.md` | Glance overview | iframe hub panel | Performance parallel load |
| `hub-storage-visual-inspector-design.md` | `/storage` treemap | `storage.html`, gp-treemap | Deep-link from archive done |
| `hub-machines-wake-sleep-design.md` | WoL/SSH | `machines_ctl.py`, overnight wake | TNAS sleep N/A; WoL when off |
| `hub-research-upcoming-design.md` | Research panel | HN/GitHub/HF presets | Scheduled LLM synthesis disabled |
| `hub-youtube-proton-design.md` | YouTube via Proton | MeTube path | Cookie maintenance |
| `hub-capture-file-type-folders-design.md` | Capture destinations | `capture-folders.json` | — |
| `hub-firefox-capture.md` | Addon | `firefox-capture-addon/` | — |
| `hub-seedvault-tnas-design.md` | Phone backups | Design locked | **Not implemented** |
| `hub-lan-services-audit-design.md` | Service audit | `QC-SYSTEM-PROCESSES.md`, scripts | Repeat audit cadence |
| `hub-chat-overnight-design.md` | Chat + overnight | Chat slug, last-night card, schedule JSON | dsh research steps off |
| `plans/hub-downloader-search-capture.md` | Implementation plan | Largely done | See TODO.md |
| `plans/hub-storage-visual-inspector.md` | Storage | Shipped | — |
| `plans/hub-vpn-header-queue-redesign.md` | VPN/queue | Shipped | — |
| `plans/hub-research-upcoming.md` | Research | R1 panel | R2 LLM overnight |
| `plans/hub-capture-file-type-folders.md` | Capture | Shipped | — |
| `plans/hub-queue-first-rescue.md` | Queue rescue | Partial | Agent review deploy parity |
| `plans/hub-youtube-proton.md` | YouTube | Partial | Cookies |
| `plans/hub-phase-d3-access-capture.md` | Access | Partial | form.html deprecation |

## Auditor actions

1. For each **Gap** marked high risk, cross-check `TODO.md` and `PROJECT-STATE.md` backlog.
2. Do not treat superpowers as normative where `agent-context/` or code disagrees — **code + PROJECT-STATE win**.
3. Propose one Action Packet per highest-risk gap (likely TV season-first UX or Sonarr deploy verification).
