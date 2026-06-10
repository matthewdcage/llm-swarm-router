# netllm-agent

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

FastAPI agent daemon: OpenAI-compatible `/v1/*`, Anthropic `/v1/messages`, admin `/netllm/v1/*`, Prometheus `/metrics`, web dashboard `/ui/`.

## Ownership

Key modules: `app.py`, `service.py`, `admin.py`, `metrics.py`, `shard.py`. Static UI: `static/` (HTML, JS, CSS, tokens).

## Local Contracts

- Default bind: `127.0.0.1:11400`; do not run menubar app and `./netllm serve` together (same port)
- Dashboard tokens: edit `apps/netllm-mac/design-tokens.json`, run `scripts/generate-dashboard-tokens.py` (CI `--check`)
- In-app update API: `GET /netllm/v1/update/check` (macOS menubar proxies this)
- **Admin routes** (`admin.py`): config save, doctor, version, logs, discover, peers-scan — allowed from **this host** (`local_admin_client_hosts()` in `netllm-core`) or `Authorization: Bearer <cluster_token>`; remote LAN clients get read-only status/models unless token is set
- **Web dashboard** (`static/dashboard.js`): status/models load without admin; doctor/config failures degrade gracefully (warn banner, not fatal)
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
