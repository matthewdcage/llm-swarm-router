# netllm, Hub Chat, and overnight automation

Audience: audit agent. Parent repo: `llm-swarm-router/AGENTS.md` (netllm mesh). Hub-specific: `llm_ctl.py`, `overnight.html`, `scripts/overnight-run.py`.

## netllm (parent monorepo)

| Item | Relation to Hub |
|------|-----------------|
| `netllm` agent `:11400` | Mac/Ubuntu/Bucket mesh for editors; **not** the Hub download worker |
| Hub Chat | Separate: iframe to DeepSeek harness / vLLM on ai-server |
| Overnight `dsh_research` steps | Disabled by default; would use LLM on ai-server when enabled |

Audit scope: document **touchpoints only** — do not re-audit entire netllm routing unless Chat/overnight LLM is in scope.

## Hub Chat (`chat.html`)

- Slug `/chat` via Caddy
- Planned/on-demand **vLLM toggle** (Qwen-class on ai-server GPU)
- iframe host: `chat.bucket-hub.stackrise.com.au` → dsh web `:3080` (see `MANUAL-STEPS.md`)
- GPU on ai-server is **mutually exclusive** with vLLM, Ollama, headless Blender — see parent `docs/linux-gpu-vram.md`

## `llm_ctl.py`

- Pi-side helper to start/stop LLM stack on ubuntu via SSH/API patterns
- Used by Chat UI and status widgets

## Overnight schedule (`overnight-schedule.default.json`)

| Step | Type | Enabled | Critical |
|------|------|---------|----------|
| wake-tnas | machine_wake | yes | no |
| wake-ubuntu | machine_wake | yes | yes |
| mount-check | mount_check | yes | yes |
| queue-drain | queue_drain | yes | no |
| deep-research | dsh_research | no | no |
| log-review | dsh_log_review | no | no |
| seedvault-sync | sync_job | no | no |
| sleep-ubuntu | machine_sleep | yes | no |

- Timezone: `Australia/Brisbane`
- Window: 22:00–06:00
- `GET /api/overnight/last-night` — merged steps + jobs for Glance card (`hub-overnight.js`)

## Auto-wake on enqueue (`app.py`)

- High-priority job → `_auto_wake_worker_if_needed()` → WoL **ubuntu** if worker heartbeat stale
- Does **not** auto-wake TNAS (archive to NAS still manual/WoL)

## Pi scheduling

- `scripts/overnight-run.py` + systemd timer on Pi (`scripts/systemd/overnight-run.service`)
- `scripts/overnight-dsh-task.sh` — dsh integration when research steps enabled

## Assumptions

- ai-server may sleep; queue drain depends on wake-ubuntu succeeding
- TNAS wake in overnight is best-effort (non-critical step) — fails if NAS hard off
- Seedvault Pi WebDAV → TNAS deferred (`SEEDVAULT.md`)
