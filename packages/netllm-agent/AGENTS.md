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
- **Failure accounting** goes through `AgentService._mark_backend_failure` (never `pool.mark_failure(backend)` directly in proxy loops): capacity errors exclude the backend for that request only and must not trip it offline
- **`auto` strategy** resolves in `_select_backend_for_request` (shard context → batch_shard, else least_load); shardless batch_shard requests bump `_shardless_fallbacks` (in `/netllm/v1/status`) and log only at 1 then every 100th — never per-request
- **Config drift:** heartbeat/status carry `routing_strategy` + `version`; `peer_config_warnings()` feeds status `peer_warnings` and doctor `notes` on mismatch
- **Cloud materialization** (`AgentService._materialize_cloud_provider_backends`, called from every proxy entry point and `list_models_aggregated`): syncs enabled `[cloud.providers.*]` entries into keyed `cloud-<id>` pool rows, skips rebuilding an unchanged row (preserves probed health/model catalog across requests), and prunes rows for providers no longer enabled via `pool.prune_cloud_provider_rows` — `cloud.enabled=False` prunes all cloud rows including the legacy `anthropic-cloud`/`openai-cloud` env-key injects, which are now also tagged with `cloud_provider`
- **`admin.config_summary`/`apply_config_patch`** treat `cloud` like `routing.backends`: keys are write-only (`api_key_set: bool` in the read side; an omitted `api_key` in a patch preserves the stored one) — never round-trip a raw key over the admin API's GET path
- **Drain** (`POST /netllm/v1/admin/drain {"draining": bool}`, admin-gated): toggles `AgentService.draining`, a runtime-only flag (never persisted to config.toml, resets to `False` on the next process start) surfaced in `status_payload()` — which `gossip_loop` sends verbatim as the heartbeat body, so every peer's `handle_heartbeat` picks it up and `SwarmRegistry.peer_agent_backends()` omits a draining peer entirely from routing candidates. Nothing here cancels requests already in flight on the draining agent — only *future* selection is affected, everywhere.
- **`agent.max_concurrency`**: self-declared per-machine cap, also carried in `status_payload()`/heartbeat; a peer's value lands on its `Backend.max_concurrency` in `peer_agent_backends()` (see `netllm-core/AGENTS.md`). Absent-tolerant both ways — an older peer's heartbeat omitting `max_concurrency`/`draining` reads as `0`/`False`.
- **`GET /netllm/v1/cloud/providers`** (`admin.cloud_provider_registry_payload`): single source of truth for cloud provider display metadata (id/display_name/notes/regions/auth_modes/default_api_format/api_key_env), consumed by the macOS app (`AgentAPI.cloudProviderRegistry`) so Swift never hand-mirrors registry text — see the "Schema triple-mirror drift" note in [docs/cloud-providers-plan.md](../../docs/cloud-providers-plan.md). Adding/editing a provider in `netllm_core.cloud_providers.CLOUD_PROVIDERS` is enough; no client-side text needs updating.
- **`GET /netllm/v1/config/schema`** (`netllm_core.config_schema.config_schema_document`, also reachable offline via `netllm config schema`): form *shape* (widget/type/options/write_only/read_only/item_schema) for the 6 editable config sections — companion to `admin.config_summary` (values). Adding a pydantic field to `NetllmConfig` needs a `json_schema_extra` hint only for non-default widget/secrecy behavior (see `tests/test_config_schema.py`'s drift check); the field then appears in the dashboard's `renderSchemaForm`-driven tabs and (for `ui`/`discovery`/`swarm`, and `routing.model_pools`) the macOS app for free. See [docs/config-schema-rewrite-plan.md](../../docs/config-schema-rewrite-plan.md).
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
