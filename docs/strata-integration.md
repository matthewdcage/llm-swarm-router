# Strata (local provider)

[Strata](https://github.com/Niko1221/Strata) is a local MoE inference engine with an OpenAI-compatible API on port **8080** by default (`--port` / `STRATA_PORT`).

netllm discovers Strata like other local providers when `strata` is listed under `[discovery].providers`. On **Linux and Windows**, `netllm init` includes `strata` in the default provider list. **macOS** does not (default oMLX also uses `:8080`); enable Strata manually in Settings or config if you run Strata without oMLX.

## Endpoints (Strata root, not `/v1` alone)

| Purpose | Method / path |
|---------|----------------|
| OpenAI chat | `POST /v1/chat/completions` |
| Anthropic messages | `POST /v1/messages` |
| Models / catalog | `GET /v1/models` |
| Load state | `GET /health` (`loaded` boolean) |
| Wake engine | `POST /load` (`Content-Type: application/json`, body `{}`) |
| Unload | `POST /unload` (same content type) |

When idle-unload is configured in Strata, `/v1/models` may list models with an **unloaded** state while `/health` reports `loaded: false`. Strata does not autoload on chat; netllm calls `POST /load` before proxying when `routing.strata_auto_load` is true (default).

## Environment

| Variable | Role |
|----------|------|
| `STRATA_PORT` | Comma-separated ports to scan (registry `port_env`) |
| `STRATA_API_KEY` | Bearer token when Strata is started with `--api-key` |

Per-URL keys can be stored in `[[routing.backends]]` like other providers.

## Coexistence with oMLX

Both default to `http://127.0.0.1:8080/v1`. When both providers are enabled, netllm fingerprints the server (oMLX admin API vs Strata `/health`) and assigns a single provider id per URL.

## Operator smoke

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/v1/models
./netllm discover
./netllm models
./netllm test
```

Upstream docs: [Strata DETAILS](https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md).
