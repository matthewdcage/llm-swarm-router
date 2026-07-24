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

## Phase 3.5 — Codex Responses API bridge (done 23/07/2026)

Discovered while wiring up the four requested reference harnesses (Codex,
Claude Code, Pi Agent, Gemini CLI/Antigravity): Codex removed
`wire_api = "chat"` support entirely as of February 2026
([openai/codex discussion #7782](https://github.com/openai/codex/discussions/7782)).
Every provider in `~/.codex/config.toml`, including a custom
`[model_providers.<id>]`, must speak the Responses API. netllm only ever
served Chat Completions — a real protocol gap, not a config tweak.

- ~~`netllm_core/openai_responses_bridge.py`: translates Responses API
  requests/responses to/from Chat Completions at the edge only (mirrors
  `anthropic_bridge.py`'s role for the Anthropic surface).
  `POST /v1/responses` delegates straight to the existing
  `proxy_chat_completion(_stream)` path, so source identity, per-source
  routing, scenario classification, and capacity control are inherited for
  free — Codex is just another attributable source.~~
- ~~Covers plain text and function-calling turns, multi-turn conversations
  replayed via `input` (Codex resends its own history each call rather than
  using `previous_response_id`).~~

**Explicitly not implemented** (would need real traffic to validate against
rather than guessing): encrypted reasoning items, image/file input blocks,
the `store`/background-response lifecycle.

**Tests (passing):** `tests/test_codex_responses_bridge.py` (15 tests) —
request translation (string/array `input`, `instructions`, content blocks,
`function_call`/`function_call_output` replay, tool shape conversion,
`max_output_tokens`/`reasoning.effort`), response translation (text,
function calls, `length` → `incomplete`), streaming event sequencing, and
an end-to-end route test confirming the existing chat pipeline sees a
normal Chat Completions payload.
**Gate met:** `./scripts/ci.sh` (lint + 515 tests) green; `basedpyright`
clean.
**Deferred to Phase 5:** the streaming half is unverified against a live
Codex session (verified only via synthetic SSE fixtures matching the
documented event shapes) — needs the real binary.

## Phase 4 — Registration UX and docs (partially done 23/07/2026)

- ~~Docs: [editor-integration.md](editor-integration.md) and
  `config.example.toml` updated with concrete, verified wiring for all four
  reference harnesses — Claude Code (native, `ANTHROPIC_BASE_URL`), Codex
  (new named provider + `wire_api = "responses"`, see Phase 3.5), Pi Agent
  (native `~/.pi/agent/models.json`, `api: "openai-completions"`), and
  Gemini CLI/Antigravity (Gemini CLI's native-protocol custom-endpoint
  support is an unresolved upstream feature request — documented as
  **not** reliably wireable today — with Antigravity's built-in "OpenAI
  Compatible" custom-model slot recommended instead).~~
- ~~Dashboard `/ui/` Sources tab (`renderSourcesTab`, `dashboard.js`) — see
  Phase 4a, done.~~
- ~~macOS Settings Sources parity (`sourceEditor`, `SchemaFormView`'s new
  `secret` widget) — see Phase 4b, done.~~
- ~~CLI: `netllm sources list|toggle <id>` (Typer, mirrors `netllm cloud`
  command shape) writing `[[sources]]` via `netllm_agent.admin.
  apply_config_patch` — see Phase 4c, done. Supersedes this bullet's
  original `list|add|remove|set` shape: `toggle` (create-if-absent,
  flip `enabled` if present) turned out to be the simpler one-action
  primitive the dashboard/macOS toggle UX (Phase 4d) also needed, so all
  three surfaces share one mental model instead of the CLI having a
  separate add/remove/set vocabulary.~~ `netllm connect <tool>`'s
  registry-aware upgrade (mint/print the per-source key inline) is still
  open — Phase 4d's skill update instead made `netllm-connect-editor`
  detection-aware without touching the CLI command itself.
- Still open: custom harness path documented generically (send
  `x-netllm-source: my-harness` or a minted key + optional `secret_env`;
  example snippet for arbitrary OpenAI/Anthropic SDK clients beyond the
  four named references).
- ~~`.agents/skills/netllm-connect-editor` update + `scripts/
  sync-agent-skills.sh`, AGENTS.md command table entry for
  `netllm sources` — see Phase 4d, done.~~
- Still open: document Option B chaining (LiteLLM/Bifrost as a
  `[[routing.backends]]` row) as the long-tail-cloud escape hatch.

**Tests:** CLI tests alongside existing cloud-CLI tests; skill sync check.
**Gate:** `ci.sh` green; `/netllm-connect` flow in Claude Code produces a
working per-source wiring end-to-end.

## Phase 4a — Dashboard Sources tab (done 23/07/2026)

`routing.sources` needed no new server-side plumbing — the schema/admin
work landed in Phase 1. What was missing was purely the JS view, plus two
generic widget gaps the schema-driven engine had never needed before:

- ~~New nav item + tab panel (`index.html`) and `renderSourcesTab`
  (`dashboard.js`), mirroring `renderRoutingTab`'s pattern for
  `routing.policies`/`routing.backends` — reuses the same
  `renderSchemaField`/`schemaFieldsCard` engine and the existing
  write-only `secret` handling (blank-means-keep, same convention as
  `swarm.cluster_token`/cloud provider `api_key`), so `buildConfigPatch`
  needed zero changes.~~
- ~~Found while building it: `model_rewrites` (`dict[str, str]`) would have
  silently rendered as an empty, uneditable sub-form if routed through the
  existing dict-of-BaseModel widget (`schemaDictOfObjectsRow` assumes
  every value is an object with its own `item_schema`; a bare string has
  none). Added a real `dict_strings` widget
  (`config_schema.py` + `schemaDictStringsRow` in `dashboard.js`) instead
  of hiding the field — genuinely reusable for any future plain
  string-keyed/string-valued config field, not a one-off patch.~~
- ~~Found while building it: `SourceConfig.match: SourceMatch` is the
  schema's first field whose value is one fixed nested model (not a list
  or dict of them) — `config_schema.py` had no branch for that at all, so
  it fell through to a bare text-input fallback that would have corrupted
  the object on any edit. Added a general `object` widget
  (`schemaNestedObjectRow`) rather than special-casing `match`.~~
- Deferred, documented rather than silently shipped imperfect: the
  `scenarios` field (dict-of-`ScenarioRule`, reusing the pre-existing
  `schemaDictOfObjectsRow`) renders and round-trips correctly but has no
  inline label in the Sources item form — `schemaDictOfObjectsRow` itself
  wasn't touched to avoid any risk to the already-shipped Model Pools tab,
  which derives its label from the surrounding tab code rather than the
  widget. A user can still use it; it's just unlabeled between "Model
  Rewrites" and "Match".

**Verified live** (not just unit tests): started a throwaway agent
instance on a scratch config/port (the real menubar-app instance stayed
untouched), added a `buzz` source through the actual browser UI, clicked
Save, confirmed `config.toml` on disk gained a correct
`[[routing.sources]]` block with `model_rewrites`/`scenarios`/`match` as
proper nested TOML tables (not corrupted), then sent a live
`curl -H "Authorization: Bearer netllm-buzz"` request and confirmed
`GET /netllm/v1/status` showed `source_requests: {"buzz": 1}` and
`scenario_requests: {"buzz:default": 1}` — full round trip, dashboard
through to live routing attribution.
**Tests (passing):** 5 new `tests/test_config_schema.py` cases covering
`sources` item schema shape, `secret` write-only, the new `dict_strings`
and `object` widgets.
**Gate met:** `./scripts/ci.sh` (lint + 520 tests) green; `basedpyright`
clean; live dashboard verification above.

## Phase 4b — macOS Settings Sources parity (done 23/07/2026)

Per the Explore report ahead of this phase: `routing.sources` had **no**
Swift model or UI at all (unlike the dashboard, where the server side was
already complete and only the JS view was missing) — this was genuinely
new work, not a template-follow.

- ~~`RoutingSection.sources: [JSONValue] = []` added to
  `NetllmConfigDocument.swift` — the same dynamic-JSONValue pattern
  `model_pools` already established (no typed `SourceConfig` Swift struct;
  rendered generically via the schema document), rather than hand-typing
  yet another one-off struct.~~
- ~~`sourceEditor(index:)` in `SettingsWindowView.swift`, index-based
  (routing.sources is a list, not a dict like model_pools) with the same
  bounds-safe `Binding` pattern already used for
  `routingPolicyEditor`/`backendOverrideEditor` (`.indices.contains`
  guards on both get and set, since SwiftUI can re-evaluate stale
  `ForEach` children after the array shrinks).~~
- ~~Added a `secret` widget case to `SchemaFormView.swift` (`SecureField`,
  same write-only "blank stays blank, non-empty overwrites" convention as
  the dashboard's `schemaSecretRow` and the server's `_source_export`/
  `apply_config_patch` handling) — the first widget added to `SchemaFormView`
  since its original migration phase.~~
- Deliberately **not** rendered (would corrupt data, not just look
  incomplete): `model_rewrites`, `scenarios`, `match` are excluded from the
  fields passed to `SchemaFormView` for each source item. `SchemaFormView`'s
  fallback for any widget it doesn't recognize is a plain text field bound
  to `.stringValue`, which is `nil` for a dict/object value — rendering
  would show an empty box, and typing into it would silently overwrite the
  structured value with a plain string. Excluding them only skips
  rendering; their existing values in the underlying `JSONValue` are left
  untouched by every other field's Binding (each writes only its own key).
  A caption in the item card tells the user to edit those three via the
  dashboard or config.toml for now.

**Found while starting this phase, fixed as standalone commits before the
Swift work** (both affect the dashboard too, not just Swift):
1. `apply_config_patch`'s sources merge never copied `scenarios` or
   `prefer_provider` from the incoming patch — editing either via the
   dashboard's new Sources tab looked like it worked in the browser draft
   but was silently dropped on save.
2. `config_summary()` (`GET /netllm/v1/config`, which hydrates both the
   dashboard's draft and would hydrate Swift's) never included
   `routing.sources` at all — a previously-saved source was invisible in
   the UI after any reload, even though it round-tripped correctly through
   save. Added `_source_export` (secret always blanked, mirroring
   `_backend_override_export`).

**Verified:** `swift build` (debug) and `scripts/verify-before-pr.sh`
(lint + 522 Python tests + Swift **release** build) both clean, no new
warnings. **Not verified:** no automated Swift test target exists for this
app (matches the rest of the codebase — verification here is
build-clean + manual use), and no tool in this environment can drive the
macOS Settings window's UI, so the editor's actual on-screen behavior
needs a manual look the next time Settings is opened.

## Phase 4c — Harness registry and detection core (done 24/07/2026)

**Reference:** `agent-buzz-slack`'s Tauri "Doctor" panel
(`/Volumes/dev-4tb/AA-GitHub/MCP/agent-buzz-slack/desktop/`) — a fork of
buzz.xyz, and the reference custom harness cited since Phase 1. Its
`managed_agents::discovery` module resolves each known runtime
(`claude-agent-acp`/`claude-code-acp`, `codex`, …) via `PATH`, a
login-shell `echo $PATH` fallback, and a hardcoded list of common install
dirs, then exposes an `AcpAvailabilityStatus` enum
(`Available | AdapterMissing | AdapterOutdated | CliMissing | NotInstalled`)
that a React `Switch` renders as an install-triggering toggle. netllm
adopts the *shape* of this UX (detect → badge → one-click enable) but not
its install action — see divergences below.

Goal: close the remaining Phase 4 "still open" items (`netllm sources
list|add|remove|set`, `netllm connect <tool>` upgrade) with a detection
layer so registering a harness is "toggle it on," not "read a doc and
copy env vars."

- **Static registry**, not persisted config — `netllm_core/known_harnesses.py`:
  a `KnownHarness{id, display_name, cli_commands: list[str], install_hint: str,
  docs_url: str | None}` list seeded from the Phase 1 deferred set:
  `claude-code`, `codex`, `gemini-cli`, `cursor`, `honcho`, `buzz`, plus a
  `custom` sentinel for anything not in the list (header/key wiring only,
  no detection). `buzz`'s entry needs no `underlying_cli` distinction like
  the Rust side has — `buzz-agent` is already zero-code-change per Phase 1.
- **`SourceConfig.known_id: str | None = None`** — new optional field,
  additive, links a configured `[[sources]]` row back to a registry entry
  for badge/detection purposes only. Old configs deserialize unchanged
  (pydantic default `None`); nothing reads this field except the new
  detection endpoint, so it changes no routing behavior.
- **Detection**: `netllm_core/harness_detection.py::detect(known: KnownHarness)`
  — `shutil.which()` over `cli_commands`, in-process cache with a short TTL
  (~5 min) keyed by id, no subprocess spawn, no filesystem writes.
  Deliberately **narrower** than buzz's resolver (no login-shell PATH
  resurrection, no hardcoded vendor install-dir scan) — start simple;
  revisit only if a Phase 5-style live validation session finds real PATH
  gaps (e.g. GUI-launched processes with a sanitized `PATH`, which
  `netllm doctor` already has to reason about for the agent itself).
- **New read-only endpoint** `GET /netllm/v1/harnesses` — merges the
  registry with current `routing.sources` state and live detection:
  `{id, display_name, configured: bool, enabled: bool, detected: bool,
  install_hint, docs_url}` per harness. Purely computed on request; does
  not touch `config.toml` and does not change `GET /netllm/v1/config` or
  `GET /netllm/v1/status` response shapes — additive, so older dashboard
  builds and the current macOS app keep working unmodified against an
  upgraded agent.
- **CLI**: `netllm sources list` (Typer, mirrors `netllm cloud` shape per
  the existing "still open" note) gains a `DETECTED` column from this
  endpoint's local equivalent; new `netllm sources toggle <id>
  [--known <registry-id>]` — if no `[[sources]]` row exists for `<id>`
  yet, creates a minimal one (`enabled=true`, `known_id` set) through the
  same `apply_config_patch` → `config_merge._merge_sources` path Phase 1
  built (do **not** duplicate that merge logic — the in-flight
  `config_merge.py` unification is exactly the seam to reuse); if one
  exists, flips `enabled`. Elevated-capability sources still hit
  `_validate_elevated_sources` — toggling one on without a secret on a
  LAN-bound agent must surface that rejection, not silently no-op.

**Deliberate divergences from the buzz reference** (safety-rule driven,
not a UX regret):
1. **No auto-install.** Buzz's toggle can shell out to `npm i -g …`/`pip
   install …` on click. This repo's tool-use rules forbid downloading or
   executing files from untrusted sources without the user running them,
   and installing a global CLI package is exactly that kind of side
   effect. netllm's toggle only ever writes to `routing.sources`; a
   not-detected harness shows a **copyable** `install_hint` string, never
   an executed one.
2. **Never force a source off.** Buzz's switch reflects live availability
   1:1. netllm's "unknown ≠ broken" and multi-machine philosophy
   (`local_spillover`, swarm peers) mean a source can be legitimately
   enabled on this agent while its CLI lives only on a peer machine or a
   remote client. Detection only changes the status chip, never the
   `enabled` value, and only a user action changes `enabled`.
3. **No auth/login-status probing** (buzz's separate `AuthStatus`:
   `claude auth status`, `codex login status`, config-file validity).
   Out of scope for this phase — flagged as a possible Phase 4e follow-on,
   not bundled here, to keep this phase's blast radius to "is the binary
   on PATH."

**Tests:** `tests/test_known_harnesses.py` (registry shape),
`tests/test_harness_detection.py` (mocked `shutil.which`, cache TTL, no
subprocess spawned), `tests/test_admin_harnesses_endpoint.py` (merges
registry + `routing.sources` + detection correctly; response shape stable
when `routing.sources` is empty — the common case per Phase 1's "nothing
pre-seeded" default), CLI tests for `sources list`/`sources toggle`
alongside the existing cloud-CLI test pattern. Full suite
(`./scripts/ci.sh`) must stay green — this phase adds one optional field
and one new GET endpoint, nothing else in the request path changes.
**Gate:** schema (`known_id` addition) + endpoint contract reviewed by
Matthew before Phase 4d frontend work starts; `ci.sh` green;
`basedpyright` clean on new/touched files.

## Phase 4d — Dashboard and macOS toggle UX (done 24/07/2026)

Parity requirement per the standing "both surfaces, always" pillar —
ships to dashboard and macOS Settings together, same as 4a/4b did.

- ~~**Dashboard Sources tab**: badge (Detected / Not detected) sourced
  from `GET /netllm/v1/harnesses`, plus a checkbox next to each known
  harness (configured or not) that mutates `state.configDraft.routing.
  sources` directly (find-or-append by `known_id`) and `markDirty()` —
  the existing Save button already sends the full `routing.sources` array
  unconditionally (`buildSchemaSectionPatch`), so no new save-path code
  was needed.~~
- ~~**macOS Settings**: `sourceEditor` gains a small detected/not-detected
  badge (`AgentAPI.harnesses`, polled every `refreshLiveData()` cycle —
  detection can change mid-session, unlike the once-per-session cloud
  provider registry) next to the existing `enabled` field. Un-registered
  known harnesses get a compact "Add & enable" row above the existing
  per-source list. No new Swift model type — stays in the existing
  dynamic-`JSONValue`/`SchemaFormView` pattern Phase 4b established.~~
- ~~**`.agents/skills/netllm-connect-editor` update**: step 1 also curls
  `GET /netllm/v1/harnesses`; step 3 branches on the result per harness —
  detected-not-enabled mentions `netllm sources toggle <id>` as optional,
  not-detected surfaces `install_hint` as copy-paste text (never
  executed), already-enabled proceeds straight to env wiring. 404 (older
  agent) falls back to the static flow unchanged. Synced via
  `scripts/sync-agent-skills.sh`. Still never auto-edits editor
  `settings.json` without explicit consent.~~
- ~~**AGENTS.md**: `netllm sources list|toggle` added to the command
  table; `GET /netllm/v1/harnesses` documented in
  `packages/netllm-agent/AGENTS.md`'s endpoint-facts list.~~

**Found while building this phase, fixed before calling it done:** the
first dashboard implementation had `renderHarnessCard` read `h.enabled`
straight from the fetched `state.harnessRegistry` snapshot. Clicking a
checkbox correctly mutated `state.configDraft.routing.sources` (so Save
worked), but the immediate re-render read the *unchanged* snapshot, so
the checkbox visibly reverted until the next full poll — caught live in a
browser check against a scratch agent, not by code review. Fixed by
having `toggleHarness` mirror the pending `enabled`/`configured` change
into `state.harnessRegistry` too, so the checkbox reflects the pending
edit immediately and Save still persists it. A reminder that "no
automated dashboard test infra" (true, unchanged) makes a real
browser pass load-bearing, not optional, for this kind of UI.

**Non-breaking guarantees — verified, not just designed:**
- Live smoke on a throwaway scratch agent (port 11499, never the real
  menubar-app instance): `GET /netllm/v1/harnesses` correctly detected
  real `claude`/`codex`/`gemini`/`cursor` binaries on this machine's PATH
  and correctly reported `honcho`/`buzz-agent` as absent — the registry's
  `cli_commands` guesses for `gemini-cli`/`cursor` turned out right here,
  though still not verified on a second machine.
- `netllm sources toggle codex` (CLI) → agent restart → `GET /netllm/v1/
  harnesses` reflected `configured: true, enabled: true` → dashboard
  checkbox toggle for `claude-code` → Save → `config.toml` on disk
  carries both sources correctly, each with its own `known_id`, neither
  disturbing the other. Full loop, not just unit tests.
- `GET /netllm/v1/config`/`GET /netllm/v1/status` confirmed unaffected by
  calling `GET /netllm/v1/harnesses` (both a unit test and a live curl
  check).
- Toggle round-trip regression test (CLI, `tests/test_cli_sources.py`)
  passing: toggling one source leaves a second source and the toggled
  source's own `model_rewrites`/`scenarios`/`match`/`secret` untouched.

**Tests/validation:** `./scripts/ci.sh` (572 tests) and
`scripts/verify-before-pr.sh` (adds Swift release build + `swift test`)
both green. Dashboard: live browser pass against a scratch agent (above),
no automated JS test infra (unchanged from 4a). macOS: `swift build -c
release` + `swift test` clean; **the Settings window itself was not
opened in this session** (no interactive macOS UI driver available here)
— still needs one real look, same honesty caveat Phase 4b logged for its
own Swift work.
**Gate met:** `ci.sh` + `verify-before-pr.sh` green; round-trip test
passing; dashboard toggle verified live end-to-end including a real bug
found and fixed. **Still open:** open macOS Settings by hand once to
confirm the badge/quick-add row renders as designed; verify `gemini-cli`/
`cursor`/`honcho` `cli_commands` against a second, differently-configured
machine (this session's machine happened to have `gemini`/`cursor` on
PATH, which is a useful data point but not a substitute for a clean
install check).

## Phase 4e — Harness logos (done 24/07/2026)

Goal: make the six known harnesses visually recognizable in the toggle UI,
not just id/name text.

- ~~Four brand marks (`claude-code`, `codex`, `gemini-cli`, `cursor`)
  pulled as-is from [simple-icons](https://github.com/simple-icons/simple-icons)
  (CC0-1.0/public domain — explicit user go-ahead obtained before
  downloading, per repo tool-use rules on fetching files), stored at
  `packages/netllm-agent/src/netllm_agent/static/icons/harnesses/<id>.svg`
  and served from the existing `/ui` static mount — no new mount, no
  packaging changes. `codex` reuses the `openai` mark (Codex has no
  distinct simple-icons entry; it's an OpenAI product). `honcho`/`buzz`
  have no published mark in that set — each gets a small generated
  monogram SVG instead (colored circle + initial), documented as such in
  `static/icons/harnesses/README.md` rather than passed off as official.~~
- ~~`admin.harness_registry_payload` adds `icon_url` per row, computed by
  fixed convention (`/ui/icons/harnesses/<id>.svg`) rather than a new
  per-entry registry field — one file per KNOWN_HARNESSES id is the only
  contract, enforced by a test that walks the registry and asserts the
  file exists (`test_every_known_harness_has_an_icon_file_on_disk`) so a
  future harness added without its icon fails CI instead of 404ing
  silently in the UI.~~
- ~~Dashboard: `renderHarnessCard` gets an `<img>` per card. simple-icons
  SVGs carry no `fill` (default black) — invisible against the dark
  theme, and unfixable via CSS `currentColor` since `<img>`-sourced SVGs
  aren't inline DOM. Fixed with a small white background chip
  (`.harness-icon`) behind every icon, verified visually against a
  scratch agent (all four brand marks legible; `honcho`/`buzz`'s own
  colored-circle monograms read fine through the same white padding).~~
- ~~macOS: `AgentAPI.harnessIcon` fetches the SVG bytes over HTTP and
  rasterizes via `NSImage(data:)` (SVG-loading has worked on macOS since
  Catalina) — no bundled duplicate copies, no change to the
  `build-icons.sh`/`Brand/` app-icon pipeline (that pipeline is
  specifically for the app's own single icon, not a fit for a growing
  per-harness set). `SettingsViewModel.harnessIcons` caches by id,
  fetched lazily on first render via `.task`, not re-fetched every poll
  (the icon set is static; only `detected`/`enabled` change per poll).
  Same white-chip treatment as the dashboard, `swift build` clean.~~

**Verified:** live curl + browser check against a scratch agent (icons
served with `image/svg+xml`, dashboard renders all four brand marks
legibly); `swift build` + `swift test` clean; full app rebuilt via
`apps/netllm-mac/Scripts/build.sh release` and reinstalled over
`/Applications/llm-swarm-router.app` via
`packaging/scripts/macos-app-install.sh --source`, confirmed healthy and
serving `icon_url` in `GET /netllm/v1/harnesses` on the real running
instance. **Still open:** the macOS Settings window itself was not opened
by a human/interactive driver in this session (same gap Phase 4d already
logged) — the icon rendering in `sourceEditor`/`unregisteredHarnessesSection`
needs one manual look next time Settings is opened.

## Phase 5 — Real-world validation and hardening (feasible subset done 23/07/2026)

What this session could actually validate, without the real CLI binaries
or a second machine: a single throwaway agent instance (scratch
config/port — the real menubar-app instance was never touched), fed
mixed traffic via curl shaped like each of the four reference harnesses,
inspecting `GET /netllm/v1/status` after each round.

- ~~Simultaneous multi-source traffic: `claude-code` (Anthropic surface,
  `x-api-key: netllm-claude-code`, small `max_tokens` + haiku model),
  `codex` (`POST /v1/responses`, `Authorization: Bearer netllm-codex`),
  `buzz` (OpenAI surface, `netllm-buzz`), and an unattributed
  `netllm-local` call — all four attributed correctly and simultaneously
  in `source_requests`/`scenario_requests` in one run.~~
- ~~Confirmed the Codex Responses bridge (Phase 3.5) composes correctly
  with source identity: a `/v1/responses` call is attributed to `codex`
  exactly like a native `/v1/chat/completions` call would be, since it
  delegates straight into `proxy_chat_completion`.~~
- **Found a real bug this way, not by inspection:** a `codex`-attributed
  request for an unconfigured model triggered cloud injection, and the
  virtual key `netllm-codex` got forwarded to OpenAI as a literal (bogus)
  API key — OpenAI correctly 401'd it, revealing that
  `_openai_api_key`/`_anthropic_api_key` only ever special-cased the
  exact string `"netllm-local"`, not the whole `netllm-` virtual-key
  namespace Phase 1 introduced. Fixed (`is_netllm_placeholder_key`,
  separate commit) and re-verified live: the same call now cleanly 404s
  ("model not found") instead of leaking a fake credential upstream.
- ~~`./netllm doctor` / `GET /netllm/v1/doctor` clean against the
  mixed-source instance (no elevated-source-without-secret warnings, as
  expected for the test config).~~

**Still open, needs the user's real fleet/CLIs/hardware** (cannot be
fabricated in this environment):
1. The actual Claude Code, Codex, and Cursor binaries pointed at a real
   netllm agent, plus a real Buzz (`buzz-agent`) fleet.
2. Buzz fleet soak: N parallel `buzz-agent` sessions (its 8-session
   concurrency is the stressor) under `strategy = "local_spillover"` +
   `max_concurrency` — verify spillover spreads across peers, the cap
   429s cleanly above it.
3. Per-source attribution across agent-hop forwards on a real two-machine
   mesh (source must be preserved on `_peer_forward_headers`, not
   re-heuristicked on the peer) — untestable with one instance.
4. `./netllm test --source <id>` smoke added to the diagnose path (CLI
   surface, not yet built — see Phase 4).
5. `./netllm doctor` warning on unknown-source traffic volume and
   scenario rules referencing models absent from the catalog (only the
   elevated-secret-missing warning exists today, from Phase 1).
6. Soak: strategy correctness under sustained `local_spillover` load with
   per-source caps; throughput regression check against baseline.

**Gate (partial):** multi-source attribution and Codex-bridge composition
confirmed live, one real bug found and fixed as a direct result. Full gate
(real fleet, multi-machine hop preservation, soak) awaits the user's
hardware — this is not something a single sandboxed session can complete
honestly.

## Explicitly out of scope

- Wrapping OAuth CLI subscriptions as backends (CLIProxyAPI pattern) beyond
  the already-sanctioned `plan_token` / OpenRouter PKCE paths — ToS risk.
- Budgets/spend tracking per source (LiteLLM-style) — possible later on top of
  per-source counters; not needed for routing.
- Auto-installing or auto-updating a harness CLI/adapter on the user's behalf
  (Phase 4c/4d deliberately diverge from the buzz.xyz reference here) — the
  toggle surfaces a copyable install command, never an executed one.
- Per-harness auth/login-status probing (`claude auth status`, `codex login
  status`, config-file validity) — buzz's `AuthStatus` concept; a possible
  Phase 4e, not bundled into the PATH-only detection in Phase 4c.
- Login-shell `PATH` resurrection or hardcoded vendor install-dir scanning for
  detection — start with `shutil.which()` only; revisit if real usage shows
  gaps.
- Replacing the front door with LiteLLM/Bifrost (research Option C, rejected).
