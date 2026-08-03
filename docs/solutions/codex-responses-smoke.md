# Codex Responses API — live smoke checklist

Manual validation for **F-39**: Codex CLI against netllm's `POST /v1/responses` bridge (`netllm_core.openai_responses_bridge`). Offline coverage lives in `tests/test_codex_responses_bridge.py` (15 tests) and contract vectors; this checklist is the live gate.

Run on a host with a local LLM backend (oMLX, Ollama, LM Studio, or vLLM) and the Codex CLI installed.

---

## Run log (fill in each pass)

| Field | Value |
|-------|-------|
| Date | |
| Operator | |
| netllm version / commit | `./netllm --version` or `git rev-parse --short HEAD` |
| Codex CLI version | `codex --version` |
| Model ID used | from `./netllm models` |
| Backend provider | oMLX / Ollama / LM Studio / vLLM |
| Non-stream curl | pass / fail |
| Codex TUI session | pass / fail |
| Streaming + tool call | pass / fail / skipped |
| `source_requests.codex` | pass / fail |
| Notes | |

---

## Prerequisites

1. **Agent running** (foreground or menubar — not both on `:11400`):

   ```bash
   cd /path/to/llm-swarm-router
   ./netllm serve
   ```

2. **Health and catalog:**

   ```bash
   ./netllm doctor
   ./netllm models
   ```

   Pick a **chat-capable** model ID from the list (not embedding/TTS). Export it for the steps below:

   ```bash
   export NETLLM_MODEL="<model-id>"
   ```

3. **Optional — register Codex as a known source** (recommended for attribution):

   ```bash
   ./netllm sources toggle codex
   ./netllm restart    # or menubar Restart Agent
   ```

   Use virtual key `netllm-codex` in the steps below. With only `netllm-local`, attribution stays on `default`.

4. **Codex CLI** on `PATH` (`codex --version`).

---

## Step 1 — Non-streaming `POST /v1/responses` (curl)

Confirms the bridge returns a completed Responses object before touching the real CLI.

```bash
export NETLLM_API_KEY=netllm-codex   # or netllm-local

curl -sS http://127.0.0.1:11400/v1/responses \
  -H "Authorization: Bearer ${NETLLM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${NETLLM_MODEL}\",\"input\":\"Reply with exactly: netllm-codex-smoke-ok\"}"
```

**Pass criteria:**

- HTTP 200
- JSON includes `status: "completed"` (or `incomplete` with non-empty `output`)
- Assistant text contains `netllm-codex-smoke-ok` (or a sensible paraphrase from the local model)

**Fail:** 404 → model ID mismatch (`./netllm models`). 502/503 → backend down or capacity; check `./netllm doctor` and agent log.

---

## Step 2 — Codex `config.toml` (Responses wire)

Codex ignores `OPENAI_BASE_URL` for custom providers. Every provider must use `wire_api = "responses"` ([openai/codex#7782](https://github.com/openai/codex/discussions/7782)).

Add or merge into `~/.codex/config.toml`:

```toml
model_provider = "netllm"
model = "<model id from ./netllm models>"

[model_providers.netllm]
base_url = "http://127.0.0.1:11400/v1"
env_key = "NETLLM_API_KEY"
wire_api = "responses"
```

Set the API key in the shell that launches Codex:

```bash
export NETLLM_API_KEY=netllm-codex   # or netllm-local
```

Shortcut — print the same block from the repo:

```bash
./netllm connect codex
```

**Pass criteria:** `model` matches `./netllm models` exactly; `wire_api = "responses"`; `base_url` ends with `/v1`.

---

## Step 3 — Streaming Codex TUI session (tool call if possible)

1. Snapshot attribution counters (baseline):

   ```bash
   curl -s http://127.0.0.1:11400/netllm/v1/status | jq '.source_requests'
   ```

2. Start Codex in the same shell (so `NETLLM_API_KEY` is set):

   ```bash
   codex
   ```

3. In the TUI, run a short turn, e.g.:

   > Say hello in one sentence.

   Confirm streaming output appears without protocol errors.

4. **Tool call (optional but preferred):** ask Codex to invoke a built-in tool, e.g.:

   > List the files in the current directory using your shell tool.

   **Pass criteria:** Codex completes the turn; if tools are enabled, a function-call round-trip succeeds (no hang, no empty stream, no JSON parse error in the TUI).

5. Exit Codex cleanly.

**Fail triage:** `./netllm doctor`; agent log (`~/Library/Application Support/netllm/logs/agent.log` on macOS menubar, or the terminal running `./netllm serve`). Streaming bugs are the main F-39 gap — offline tests use fixture SSE only.

---

## Step 4 — Verify harness attribution after session

Source attribution is exposed on **status**, not inside the telemetry payload. The web **Serving** tab merges `GET /netllm/v1/status` (`source_requests`) with `GET /netllm/v1/telemetry` (router token/request counters).

After Step 3:

```bash
curl -s http://127.0.0.1:11400/netllm/v1/status | jq '.source_requests'
```

**Pass criteria:**

- With `netllm-codex` and `[[routing.sources]]` id `codex` enabled: `"codex"` key present with count ≥ 1
- With `netllm-local` only: count may appear under `"default"` (still proves routing; re-run with `netllm-codex` for named attribution)

Optional — router session counters incremented (telemetry):

```bash
curl -s 'http://127.0.0.1:11400/netllm/v1/telemetry?scopes=router&history=0&watch=0' \
  | jq '.router.session.requests'
```

Optional — dashboard: open http://127.0.0.1:11400/ui/ → **Serving** → **Requests by source (harness)** shows `codex`.

Optional — Prometheus (if scraped):

```bash
curl -s http://127.0.0.1:11400/metrics | grep 'netllm_source_requests_total{source="codex"'
```

---

## Step 5 — Record versions and sign off

Before closing F-39 / roadmap item B9:

```bash
codex --version
./netllm --version 2>/dev/null || git describe --tags --always
date -u +%Y-%m-%d
```

Fill the **Run log** table at the top of this file (or paste results in the PR / release notes).

**Gate:** all required steps pass on at least one maintainer machine with a real Codex binary. CI does not run Codex; offline tests alone do not close F-39.

---

## Related

- Wiring reference: [editor-integration.md](../editor-integration.md#codex)
- Bridge design + offline tests: [cli-source-routing-plan.md](../cli-source-routing-plan.md) Phase 3.5
- CLI helper: `./netllm connect codex` (`--print-env`, `--json`)
- Findings register: F-39 in [architecture/07-findings-register.md](../architecture/07-findings-register.md)
