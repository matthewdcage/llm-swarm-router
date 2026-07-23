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
6. **Attributive by default; enforced where it matters.** A bare `netllm-<source>`
   key labels traffic without being checked — matches today's trust boundary
   (loopback bind / `swarm.cluster_token`), keeps onboarding a two-env-var
   change, and avoids a new silent-401 failure mode. But any source config that
   grants elevated capability (`allow_cloud = true`, a cloud provider
   allowlist, or a `max_concurrency` above the global default) **must** carry a
   `secret`/`secret_env` once the agent binds beyond loopback — `apply_config`
   rejects saving such a source without one on a LAN-bound agent, and
   `netllm doctor` flags it if the bind changes after the fact. This bounds
   spoofing to "cheaper local routing," never cloud-key or budget exposure.
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

## Phase 1 — Source identity core (done 23/07/2026)

- ~~`netllm-core`: add `SourceConfig` (+ `SourceMatch`) to `models.py`, nested
  as `routing.sources: list[SourceConfig]` (not a new top-level section —
  reuses the generic list-of-BaseModel walk in `config_schema.py` that
  already covers `routing.policies`/`routing.backends`, so no schema-document
  code changes were needed); add `resolve_source(headers, sources)` in new
  `netllm_core/source_identity.py`.~~
- ~~`netllm-agent`: resolve + count source once per proxy entry point via
  `AgentService._attribute_source` (chat, chat-stream, embeddings, messages,
  messages-stream — all five call sites); expose counts via
  `SOURCE_REQUESTS_TOTAL{source,resolved_via}` in `/metrics` and
  `source_requests` in `GET /netllm/v1/status`.~~ (`in_flight`/`last_seen` per
  source deferred to Phase 2, where per-source concurrency caps need them.)
- ~~Virtual-key parsing: `netllm-<source>` (or `netllm-<source>.<secret>`)
  recognized on both the `Authorization: Bearer` and `x-api-key` header
  forms; the `netllm-local` sentinel always resolves to `default`.~~
- ~~Security tightening beyond the original bullet: a source's `secret` gates
  **every** attribution path (header, key, User-Agent), not only the key —
  otherwise a bare `x-netllm-source: <elevated-id>` header would have won an
  elevated identity with no proof at all. See `SourceConfig` docstring and
  `source_identity.resolve_source`.~~
- ~~Enforcement gate: `admin._validate_elevated_sources`, called from
  `apply_config_patch`, rejects saving a `routing.sources` entry with
  `allow_cloud=true`, a non-empty `cloud_providers` allowlist, or
  `max_concurrency` above `routing.max_in_flight_per_backend` unless
  `secret`/`secret_env` is set **and** `agent.listen` is LAN-reachable.~~
- ~~`secret` is write-only on the admin patch path (same convention as
  `swarm.cluster_token` / cloud provider `api_key`): omitting it on a later
  patch keeps the stored value instead of blanking it.~~
- Deferred to Phase 4: seed registry of built-in source ids with UA
  heuristics (`claude-code`, `codex`, `gemini-cli`, `cursor`, `honcho`,
  `buzz`, `custom`) — Phase 1 ships the mechanism only; no sources are
  pre-configured, so an upgraded agent's behavior is unchanged until the user
  (or `netllm connect`) adds one. `buzz` is the reference custom harness
  ([cli-routing-research.md](cli-routing-research.md) §Buzz): `buzz-agent`
  needs no code changes — key `netllm-buzz` on either surface identifies it.

**Tests (passing):** `tests/test_source_identity.py` (15 tests) — header wins
over key wins over UA for unprotected sources; `netllm-local` → `default`;
disabled sources never match; a secret gates all three paths, not just the
key; wrong/missing secret falls back to `default` rather than a 401;
elevated-capability source without a secret is rejected on a LAN-bound agent
but accepted on loopback; `secret` write-only round-trip; both surfaces
attribute identically; hot-apply of a new `routing.sources` entry via
`AgentService.apply_config` takes effect without restart.
**Gate met:** `./scripts/ci.sh` (lint + 460 tests) green; `basedpyright` clean
on all touched files.

## Phase 2 — Per-source routing overrides (done 23/07/2026)

- ~~`resolve_routing` applies a matched source's `strategy` / `local_only` /
  `allow_cloud` / `prefer_provider` **after** the policy match (source ranks
  above `routing.policies` in precedence) — a source can reopen cloud access
  a matching policy would otherwise deny, or force local-only over a policy
  that allows cloud; `RoutingPolicy` gained an optional `source` field so a
  policy can be scoped to one caller (empty = matches any, unchanged for
  existing configs).~~
- ~~`source.cloud_providers`: non-empty list narrows cloud-tagged backend
  candidates to that allowlist in `pool.select_backend`
  (`cloud_provider_allowlist` param) — never excludes local/peer rows.~~
- ~~`model_rewrites`: per-source requested-name → concrete-name mapping
  applied before `model_aliases`/`model_pools` resolution on both surfaces;
  the client-facing response always echoes the originally requested name
  (`requested_model`), so rewriting is invisible to the caller across
  retries/failover.~~
- ~~Per-source `max_concurrency`: enforced as admission control
  (`SourceCapacityExceeded` → HTTP 429), not queuing — mirrors the existing
  per-backend back-pressure cap but tracked per source across all its
  attempts/retries (`AgentService._source_in_flight`).~~
- Found and fixed during implementation: `source.allow_cloud=True` initially
  only cleared `allow_cloud_inject`, not a `local_only=True` a matching
  policy had already set — leaving a contradictory resolved state
  (`allow_cloud_inject=True` with `local_only=True`, which selection would
  still treat as local-only). Fixed so `allow_cloud` also clears `local_only`
  (caught by `test_source_allow_cloud_reverses_policy_forcing_local`).
- Deferred: the streaming Anthropic Messages path (`proxy_messages_stream`)
  applies `model_rewrites` to routing/selection and the upstream payload
  correctly, but does not rewrite the model string echoed inside individual
  SSE event bodies back to `requested_model` (the non-stream path and the
  OpenAI stream path do). No shipped source uses `model_rewrites` yet, so
  this has no current impact; revisit if a source configures it.

**Tests (passing):** `tests/test_source_routing.py` (18 tests) — strategy/
local_only/allow_cloud/prefer_provider precedence and reversal cases; the
header-as-absolute-ceiling case; source-scoped vs. unscoped policy matching;
`cloud_provider_allowlist` filtering (excludes non-allowlisted cloud rows,
never excludes local); `model_rewrites` unit coverage; capacity admission
control (under cap / at cap raises / release frees a slot / uncapped never
raises); cloud master switch still wins over `source.allow_cloud`.
**Gate met:** `./scripts/ci.sh` (lint + 476 tests) green.
**Gate:** `ci.sh` green; live check — two curl clients with different source
keys hit different backends for the same requested model.

## Phase 3 — Scenario routing (claude-code-router pattern) (done 23/07/2026)

- ~~`netllm_core/scenarios.py`: `classify_scenario()` maps each request into
  `long_context` / `web_search` / `think` / `background` / `default` from
  observable signals, checked in that priority order: estimated prompt size
  (chars/4 heuristic, no tokenizer) over a threshold (default 32K tokens) →
  `long_context`; a web-search-shaped tool in `tools` → `web_search`;
  Anthropic `thinking.type == "enabled"` or an OpenAI `reasoning_effort`/
  `reasoning` field → `think`; small `max_tokens` paired with a haiku/mini/
  flash/nano-class requested model, or a `claude-code` User-Agent, →
  `background`.~~
- ~~`ScenarioRule` (`{model, strategy, local_only, allow_cloud}`) lives in
  `SourceConfig.scenarios: dict[str, ScenarioRule]` (reuses the existing
  dict-of-BaseModel schema/dashboard widget already used by
  `routing.model_pools` — no new schema code needed). `resolve_routing`
  gained a `scenario` param: the matched rule is applied after source
  defaults and before header overrides, matching the Phase 0 precedence
  (header > scenario rule > source defaults > policy > global).~~
- ~~`ScenarioRule.model` is applied in `AgentService._apply_scenario_model`,
  layered after `model_rewrites` — a scenario can pick a different concrete
  model than the source's general rewrite (e.g. a cheaper model
  specifically for `background`).~~
- ~~Classified and counted once per proxy entry point
  (`_classify_and_record_scenario`, mirroring `_attribute_source`) on both
  surfaces; exposed as `netllm_scenario_requests_total{source,scenario}` and
  `scenario_requests` (`"<source>:<scenario>"` → count) in
  `GET /netllm/v1/status`.~~
- Deferred: the `x-netllm-scenario` **response header** the original bullet
  called for was not built. netllm sets no response headers anywhere today
  (no precedent to follow), and wiring one through both the JSON and SSE
  streaming response paths — with zero current consumers — wasn't worth the
  risk under time pressure; status/metrics already give equivalent
  visibility for tuning. Revisit if a client wants to read it directly.
- Deferred (same gap noted in Phase 2): the streaming Anthropic Messages
  path does not rewrite the model string inside individual SSE event bodies
  back to the client's requested name when a scenario rule changes it.

**Tests (passing):** `tests/test_scenarios.py` (22 tests) — classification
per signal on both surfaces, configurable threshold, priority ordering
(long_context > web_search > think > background), scenario rule vs. source
default vs. header precedence, scenario-only-applies-when-matching, and the
`AgentService` classify/count/model-override helpers.
**Gate met:** `./scripts/ci.sh` (lint + 499 tests) green; `basedpyright`
clean on all touched files.
**Live validation still open:** the plan's original gate (Claude Code
plan-mode traffic landing on a strong model, sub-agent traffic on a cheap
one, observed via status counters) needs a real Claude Code session against
a configured source and is deferred to Phase 5, which has access to actual
CLI traffic.

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
3. Verify per-source attribution across agent-hop forwards (source must be
   preserved on `_peer_forward_headers`, not re-heuristicked on the peer).
4. `./netllm test --source <id>` smoke added to the diagnose path;
   `./netllm doctor` warns on: unknown-source traffic volume, an
   elevated-capability source missing a required secret on a LAN-bound agent,
   scenario rules referencing models absent from the catalog.
5. Soak: strategy correctness under `local_spillover` with per-source caps;
   confirm no throughput regression vs. baseline (`netllm test` latency
   before/after within noise).
6. `scripts/verify-before-pr.sh` before each phase merge; release notes entry.

**Gate:** all four clients attributed correctly in status during a mixed run;
doctor clean; soak shows no regression. Then update AGENTS.md Learned
Workspace Facts with the shipped surface.

## Explicitly out of scope

- Wrapping OAuth CLI subscriptions as backends (CLIProxyAPI pattern) beyond
  the already-sanctioned `plan_token` / OpenRouter PKCE paths — ToS risk.
- Budgets/spend tracking per source (LiteLLM-style) — possible later on top of
  per-source counters; not needed for routing.
- Replacing the front door with LiteLLM/Bifrost (research Option C, rejected).
