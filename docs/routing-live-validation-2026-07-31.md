# Routing live validation — 2026-07-31

Local build **0.4.5.0** (WIP: request-aware pools, hop `exact_model_only`) installed to `/Applications/llm-swarm-router.app` on Mac mini gateway (`098fceb0`, `10.0.0.32`).

**Payload adaptation:** SDK-unknown params (`top_k`, `min_p`, etc.) are adapted once at `netllm-sdk-openai/payload.py` (terminating hop via SDK client). No duplicate core-layer adapter.

## Build & install

```bash
apps/netllm-mac/Scripts/build.sh release
packaging/scripts/macos-app-install.sh --source apps/netllm-mac/build/Stage/llm-swarm-router.app
```

Install script: graceful quit → port 11400 cleared → replace → launch → `/health` + `/ui/` verified.

## Smoke results (Mac mini gateway)

| Test | Result |
|------|--------|
| `GET /health` | PASS |
| `qwen3-next-80b` chat | PASS → Linux peer (`peer:93595960`) |
| `qwen3-next-80b` + `top_k: 40` | PASS (provider payload adapter; was SDK error on old build) |
| `qwen-next-80b` (alias) | PASS after adding `[routing.model_aliases]` |
| `gemma4:26b` | PASS → `gemma4:26b`, not qwen substitution |
| Pin `x-netllm-backend: peer:93595960` | PASS |
| Stream + `top_k` | PASS |
| Pressure 6 concurrent (3× qwen + 3× gemma) | PASS (~18s) |

Telemetry after pressure (`GET /netllm/v1/telemetry?scopes=router`):

- `peer:93595960`: qwen traffic
- Local oMLX: gemma traffic
- No gemma-on-qwen misroutes observed in responses

Script: `scripts/live-routing-smoke.sh`

## Issues found & resolution

### 1. Missing model alias (config — fixed on gateway)

**Symptom:** `qwen-next-80b` hit pool overflow and returned HTTP 200 with wrong model id instead of resolving to `qwen3-next-80b`.

**Fix applied (Mac mini):**

```toml
[routing.model_aliases]
"qwen-next-80b" = ["qwen3-next-80b"]
"gemma4:26b" = ["gemma-4-26b-a4b-it-4bit", "gemma4:26b"]
```

Via `POST /netllm/v1/admin/config` (hot-applied, persisted to `~/.config/netllm/config.toml`).

**Action:** Sync same aliases on MacBook Pro and Linux; remove bundled `alias-2` that groups gemma + qwen.

### 2. MacBook Pro config drift (open — manual sync)

**Observed on `10.0.0.12`:**

- `alias-2` = `['gemma-…', 'qwen3-next-80b']` (bad — unrelated models bundled)
- `catch-all` pool still uses old heterogeneous catch-all naming
- Strategy `auto` vs gateway `round_robin` (doctor note)

**Action:** Align MBP `config.toml` with gateway: single heterogeneous `pool-2`, separate aliases, `default_strategy = "auto"` or match gateway intentionally.

### 3. Doctor warnings (non-blocking)

- `require_token_for_inference = false` while cluster token set — enable for untrusted LAN
- Strategy mismatch vs peers — align to `auto` recommended for interactive traffic

### 4. Smoke script pressure phase

12× concurrent 80B requests can exceed tool timeout; reduced to 6× with `max_tokens: 5`. Use `scripts/live-routing-smoke.sh` or manual loop for heavier load.

## Unit tests

```bash
uv run pytest tests/test_model_pools.py tests/test_agent_hop_routing.py \
  tests/test_routing_hardening.py -q
```

44 passed (pre-PR gate).

## Production readiness checklist

- [x] Request-aware pool (no gemma steal on qwen)
- [x] `top_k` / provider payload on gateway → peer hop
- [x] Local DMG build + install verified
- [x] Live curl + pressure on Mac mini gateway
- [x] Gateway config aliases applied
- [ ] MacBook Pro config sync + rebuild/install
- [ ] Linux peer rebuild (if not running same WIP)
- [ ] Enable `require_token_for_inference` if desired
- [ ] Commit + PR on feature branch

## Next: PR scope

Code (integration branch `fix/routing-pools-integrated`):

- `packages/netllm-core/src/netllm_core/model_resolution.py` — `ModelResolver`, `allow_group_overflow`, `exact_model_only`
- `packages/netllm-core/src/netllm_core/pool.py` — two-phase request-aware overflow
- `packages/netllm-agent/src/netllm_agent/request_plan.py` — `exact_model_only` field
- `packages/netllm-agent/src/netllm_agent/service/{policy,selection,engine,surfaces/*}.py` — hop wiring
- **Removed:** `provider_payload.py` (duplicate of SDK `payload.py` — adaptation stays SDK-only)
- Tests + `config.example.toml` / dashboard / Settings help text
- `scripts/live-routing-smoke.sh` — maintainer live validation

Not in PR: user `~/.config/netllm/config.toml` (applied locally only).
