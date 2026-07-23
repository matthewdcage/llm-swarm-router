# CLI source routing plan — known sources, per-source policy, scenario routing

Plan drafted: 23/07/2026. Research grounding: [cli-routing-research.md](cli-routing-research.md).
Decision: build source identity natively in netllm (research Option A); document
external-gateway chaining (Option B) as an escape hatch only.

## Problem statement

Every CLI and harness pointed at netllm today is anonymous. The agent reads only
`x-netllm-strategy` / `x-netllm-backend` / `x-netllm-local-only` / `x-netllm-hops`
(`netllm_core/models.py:44-51`) plus the request body; the API key is a discarded
placeholder (`"netllm-local"` sentinel in `netllm_agent/service.py`), and
`[[routing.policies]]` can match only `model_prefix` and `api_format`
(`netllm_core/routing_policy.py:34-49`). So Claude Code, Codex, Cursor, Honcho,
and a custom harness are indistinguishable, and none of them can have durable,
configurable routing behavior of their own.

Goal: a CLI or custom harness registers as a **known source** with a durable
`[[sources]]` config entry controlling its routing (strategy, model rewrites,
scenario rules, local-only, cloud access, concurrency), attributable end-to-end
in status/metrics/dashboard, with zero client-side changes beyond the base-URL
and key env vars the tools already set.

## Design pillars

1. **Identity resolution order** (first hit wins), computed once per request in
   the agent layer and threaded through `ResolvedRouting`:
   1. `x-netllm-source: <id>` header (custom harnesses, explicit).
   2. Virtual key: `Authorization: Bearer netllm-<source>[.<secret>]` or
      `x-api-key` equivalent. Plain `netllm-local` stays valid → source
      `default` (full backward compatibility).
   3. Heuristic fallback (`User-Agent` / surface): e.g. Anthropic Messages
      surface + `anthropic-*` UA → `claude-code`. Heuristics only set identity;
      they never gate access.
3. **Sources are config, not accounts.** A `[[sources]]` pydantic section in
   `NetllmConfig` (same hot-apply path as everything else via
   `POST /netllm/v1/admin/config` → `apply_config`). Optional per-source
   `secret`/`secret_env` for callers that want the key to actually authenticate.
4. **Both surfaces, always.** Every feature lands on `/v1/chat/completions`,
   `/v1/embeddings`, and `/v1/messages` in the same phase — parity is a
   standing gate (the phase-1 routing-hardening lesson).
5. **Unknown ≠ broken.** An unmatched caller routes exactly as today under the
   implicit `default` source.

## Phase 0 — Ground truth and contract (gate: design sign-off, no code)

- Write `SourceConfig` schema draft: `id`, `enabled`, `description`,
  `secret_env`, `strategy`, `local_only`, `allow_cloud`, `max_concurrency`,
  `model_rewrites: dict[str, str]`, `scenarios: dict[str, ScenarioRule]`,
  `match: {header, key_prefix, user_agent_contains, api_format}`.
- Decide precedence with existing knobs and document it in the config example:
  per-request `x-netllm-*` headers > source scenario rule > source defaults >
  `[[routing.policies]]` > `[routing]` globals. (Policies keep working; a
  source is evaluated first and can delegate.)
- Confirm no regression baseline: `./scripts/ci.sh` green on main.

**Gate:** schema + precedence reviewed by Matthew; `ci.sh` green.

## Phase 1 — Source identity core (not yet implemented)

- `netllm-core`: add `SourceConfig` + `SourcesConfig` to `models.py`; extend
  `config_schema.py` so the dashboard form renders sources; add
  `resolve_source(headers, api_key, api_format, user_agent, sources)` in a new
  `netllm_core/source_identity.py`.
- `netllm-agent`: resolve source at the top of each proxy route (`app.py`
  chat/embeddings/messages handlers) and pass it into
  `service._resolved_routing`; expose per-source counters in `/metrics` and
  `GET /netllm/v1/status` (requests, in_flight, last_seen, resolved_via).
- Virtual-key parsing: accept `netllm-<source>` keys everywhere the
  `netllm-local` sentinel is currently special-cased (cloud passthrough
  extraction in `service.py` must not treat `netllm-<source>` as a real
  upstream key).
- Seed registry of built-in source ids with UA heuristics: `claude-code`,
  `codex`, `gemini-cli`, `cursor`, `honcho`, `buzz`, `custom`. `buzz` is the
  reference custom harness ([cli-routing-research.md](cli-routing-research.md)
  §Buzz): `buzz-agent` needs no code changes — key `netllm-buzz` on either
  surface identifies it (its OpenAI `Auto` mode already sends Chat Completions
  to non-openai.com hosts).

**Tests (must pass):** new `tests/test_source_identity.py` — header wins over
key wins over UA; `netllm-local` → `default`; secret mismatch rejected only
when a secret is configured; both surfaces attribute identically; hot-apply of
a new `[[sources]]` entry takes effect without restart (pattern from
`tests/test_routing_hardening.py::test_apply_config_hot_syncs_pool`).
**Gate:** `./scripts/ci.sh` green; `./netllm status` shows per-source rows on a
live agent with two differently-keyed curl clients.

## Phase 2 — Per-source routing overrides (not yet implemented)

- Extend `ResolvedRouting` resolution (`routing_policy.py:52-120`): apply the
  matched source's `strategy` / `local_only` / `allow_cloud` /
  `prefer_provider` before policy matching; add optional `source` match field
  to `RoutingPolicy` so existing policy machinery can also scope by source.
- `model_rewrites`: per-source requested-name → concrete-name mapping applied
  before `model_aliases` / `model_pools` resolution, so e.g. Claude Code's
  `claude-sonnet-5` can land on `qwen3:32b` for one source only.
- Per-source `max_concurrency`: enforced alongside
  `routing.max_in_flight_per_backend` in `pool.select_backend`
  back-pressure (429 with a clear body when exceeded, mirroring capacity
  rejection handling).

**Tests:** extend `tests/test_routing_policies.py` (source-scoped policy
matching); new `tests/test_source_routing.py` — rewrite applied per source and
not globally; local_only source never selects peer/cloud; concurrency cap
returns 429; header override still wins over source config.
**Gate:** `ci.sh` green; live check — two curl clients with different source
keys hit different backends for the same requested model.

## Phase 3 — Scenario routing (claude-code-router pattern) (not yet implemented)

- `netllm_core/scenarios.py`: classify each request into
  `default` / `background` / `think` / `long_context` / `web_search` from
  observable signals: estimated prompt tokens over a threshold (default 32K) →
  `long_context`; Messages `thinking` param / OpenAI `reasoning_effort` →
  `think`; small `max_tokens` + haiku-class requested model or Claude Code
  sub-agent UA markers → `background`; web-search tool present in `tools` →
  `web_search`.
- `ScenarioRule` per source: `{model?, strategy?, local_only?, allow_cloud?}`;
  resolved between per-request headers and source defaults per the phase-0
  precedence.
- Surface the chosen scenario in the response header
  (`x-netllm-scenario`) and per-source metrics for tuning.

**Tests:** new `tests/test_scenarios.py` — classification unit tests per
signal (both surfaces); scenario rule overrides source default; threshold
configurable; no classification cost when a source defines no scenarios.
**Gate:** `ci.sh` green; live validation with Claude Code: plan-mode traffic
(`think`) hits the configured strong model while sub-agent background traffic
hits the cheap one, verified via `x-netllm-scenario` + status counters.

## Phase 4 — Registration UX and docs (not yet implemented)

- CLI: `netllm sources list|add|remove|set <id> key value` (Typer, mirrors
  `netllm cloud` command shape) writing `[[sources]]` via the same config
  path; `netllm connect <tool>` upgraded to mint/print the per-source key and
  the exact env/config wiring per tool (replaces manual steps; never edits
  editor settings without consent, per repo rules).
- Custom harness path documented: send `x-netllm-source: my-harness` (or use a
  minted key) + optional `secret_env`; example snippet for OpenAI and
  Anthropic SDKs.
- Dashboard `/ui/`: Sources tab (list, live counters, enable/disable) using
  the existing config-schema-driven form machinery; macOS Settings parity can
  trail one release.
- Docs: update [editor-integration.md](editor-integration.md),
  `config.example.toml`, AGENTS.md command table,
  `.agents/skills/netllm-connect-editor` (then `scripts/sync-agent-skills.sh`).
- Document Option B chaining (LiteLLM/Bifrost as a `[[routing.backends]]`
  row) as the long-tail-cloud escape hatch.

**Tests:** CLI tests alongside existing cloud-CLI tests; dashboard schema test
extension (`tests/test_dashboard_config_schema.py`); skill sync check.
**Gate:** `ci.sh` green; `/netllm-connect` flow in Claude Code produces a
working per-source wiring end-to-end.

## Phase 5 — Real-world validation and hardening (not yet implemented)

Runbook on the actual fleet (two-machine mesh minimum):

1. Wire Claude Code (Anthropic surface, `netllm-claude-code` key), Codex
   (OpenAI surface), Cursor, and **Buzz** (`buzz-agent` fleet with
   `netllm-buzz` key) simultaneously; run mixed traffic.
2. Buzz fleet soak: N parallel `buzz-agent` sessions (its 8-session
   concurrency is the stressor) under a `buzz` source configured with
   `strategy = "local_spillover"` and a `max_concurrency` cap — verify
   spillover spreads across peers, the cap 429s cleanly above it, and
   attribution stays `buzz` on hop-forwarded requests.
2. Verify per-source attribution across agent-hop forwards (source must be
   preserved on `_peer_forward_headers`, not re-heuristicked on the peer).
3. `./netllm test --source <id>` smoke added to the diagnose path;
   `./netllm doctor` warns on: unknown-source traffic volume, a configured
   source with a secret but callers arriving unauthenticated, scenario rules
   referencing models absent from the catalog.
4. Soak: strategy correctness under `local_spillover` with per-source caps;
   confirm no throughput regression vs. baseline (`netllm test` latency
   before/after within noise).
5. `scripts/verify-before-pr.sh` before each phase merge; release notes entry.

**Gate:** all four clients attributed correctly in status during a mixed run;
doctor clean; soak shows no regression. Then update AGENTS.md Learned
Workspace Facts with the shipped surface.

## Explicitly out of scope

- Wrapping OAuth CLI subscriptions as backends (CLIProxyAPI pattern) beyond
  the already-sanctioned `plan_token` / OpenRouter PKCE paths — ToS risk.
- Budgets/spend tracking per source (LiteLLM-style) — possible later on top of
  per-source counters; not needed for routing.
- Replacing the front door with LiteLLM/Bifrost (research Option C, rejected).
