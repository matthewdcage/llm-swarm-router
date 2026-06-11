# netllm-agent

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

FastAPI agent daemon: OpenAI-compatible `/v1/*` (chat, models, embeddings), Anthropic `/v1/messages`, admin `/netllm/v1/*`, Prometheus `/metrics`, web dashboard `/ui/`.

## Ownership

Key modules: `app.py`, `service.py`, `admin.py`, `metrics.py`, `shard.py`. Static UI: `static/` (HTML, JS, CSS, tokens).

## Local Contracts

- Default bind: `127.0.0.1:11400`; do not run menubar app and `./netllm serve` together (same port)
- Dashboard tokens: edit `apps/netllm-mac/design-tokens.json`, run `scripts/generate-dashboard-tokens.py` (CI `--check`)
- In-app update API: `GET /netllm/v1/update/check` (macOS menubar proxies this)
- **Admin routes** (`admin.py`): config save, doctor, version, logs, discover, peers-scan — allowed from **this host** (`local_admin_client_hosts()` in `netllm-core`) or `Authorization: Bearer <cluster_token>`; remote LAN clients get read-only status/models unless token is set
- **Doctor** (`doctor_payload`): open LAN without `cluster_token` is informational (`notes`), not an `issues` failure; secured pairing is optional via Settings / CLI
- **Web dashboard** (`static/dashboard.js`): status/models load without admin; doctor/config failures degrade gracefully (warn banner, not fatal); cluster token label shows **open (trusted LAN)** when unset
- **Loop guard:** every forward to a `peer:` backend sets `x-netllm-local-only: 1` (`AgentService._peer_forward_headers`) so peers serve locally and never re-forward; status/catalog handlers force-probe **local** backends only (peer rows stay fresh via heartbeats — probing them recurses)
- **`POST /v1/embeddings`** (`proxy_embeddings`): same selection/failover loop as chat incl. agent-hop to peers; Anthropic-format backends are excluded (no Anthropic embeddings standard); unknown model 404 lists embedding-capable models first
- **Capability gate:** chat/Messages requests against non-chat models (`netllm_core.capabilities`) return 400 with a `/v1/embeddings` hint — never burn the retry budget on encoders; `/v1/models` entries carry a `capability` field
- **Retry exclusion:** chat/stream/embeddings loops track per-request failed backend ids and pass `exclude_ids` so retries reach untried peers; upstream API keys resolve via `backend.resolve_api_key()` (env fallbacks incl. `LMSTUDIO_API_KEY`)
- **Peers-scan rows** are deduped by `agent_id` in discovery and flagged `self` in `peers_scan_payload` (dashboard labels "this machine", shows `also_reachable_at` for multi-homed hosts)
- Depends on all other workspace packages except `netllm-cli`

## Work Guidance

- HTTP handlers stay thin; routing logic delegates to `netllm-core`
- Streaming and shard paths are performance-sensitive — add integration tests in `tests/`
- Dashboard changes should match macOS menubar design tokens when applicable

## Verification

```bash
./netllm serve
./netllm test
./netllm test --api anthropic
curl -s http://127.0.0.1:11400/ui/
```

## Child DOX Index

None — UI assets live under `src/netllm_agent/static/`.
