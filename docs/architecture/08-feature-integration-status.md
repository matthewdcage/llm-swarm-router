# 08 · Feature integration status

Product-facing view: what is fully shipped, what exists in the engine but cannot be reached
from a UI, and what is code with no user at all. Verified by grepping each client surface
(`dashboard.js`, `apps/netllm-mac/Sources`, `netllm_cli/main.py`) for the feature's config keys
and endpoints.

## Legend

| Mark | Meaning |
|------|---------|
| ✅ | Fully usable from that surface |
| ◐ | Partially usable — present but incomplete or read-only |
| ⬚ | Not exposed; the feature works but this surface cannot reach it |
| — | Not applicable to that surface |

## API surfaces (client-facing)

Every row changed observably in the F-24/F-25/F-26 consolidation; the operator-facing
detail is in [refactor/RELEASE-NOTES.md](refactor/RELEASE-NOTES.md).

| Surface | Status | What changed for clients |
|---------|--------|--------------------------|
| `POST /v1/chat/completions` (± stream) | ✅ | `batch_shard` failover order and attempt count (D17). Streaming errors reach the client as a real HTTP status instead of 200 + aborted body. |
| `POST /v1/responses` (± stream) | ✅ | Streamed Responses requests now record success telemetry — previously `routed_requests`, token counters and latency showed errors but never successes (D16). No wire change. |
| `POST /v1/embeddings` | ✅ | **New 400 capability guard**: a chat-only model sent to `/v1/embeddings` is now rejected up front instead of burning the whole retry budget (D4). Shard context is now honoured (D5). |
| `POST /v1/messages` (± stream) | ✅ | Upstream 400/404 from a translated OpenAI-format backend is forwarded instead of flattened to 502 (D11). Streamed responses restore the requested model name on `message_start`, and a mid-stream error frame now has its `message_stop` terminator (D9). Shard context is now honoured (D5). |
| `GET /netllm/v1/telemetry` | ✅ | `docs/telemetry-api.md` is normative and CI-gated; the previously-undocumented `subscribers` key is now documented (F-49). |

## Routing engine

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| 8 routing strategies | ✅ | ✅ | ✅ | ✅ | |
| Per-request headers (`x-netllm-strategy`, `-backend`, `-local-only`, `-hops`) | ✅ | — | — | — | Protocol-level; documented in `routing-hardening-plan.md` |
| Failover with per-request exclusion | ✅ | — | — | — | One engine loop for every surface; retry budget is `CandidateSchedule.max_attempts` (F-24 fixed, `a4c8893`…HEAD) |
| Capacity-error classification (409/429/503/507 + body markers) | ✅ | — | — | — | Surfaced as `capacity_rejections` in status |
| `routing.model_aliases` | ✅ | ⬚ | ✅ | ✅ | No CLI command; edit via config or a UI. Matched by the single `ModelResolver` walk (F-25 fixed) |
| `routing.model_pools` | ✅ | ⬚ | ✅ | ✅ | Config-only when the plan was written; both UIs now render it. Parses into `ModelGroup`; the invoked upstream name is now always one the backend advertises (D18 — see [refactor/RELEASE-NOTES.md](refactor/RELEASE-NOTES.md)) |
| `routing.policies` | ✅ | ⬚ | ✅ | ✅ | `source` scope survives a save (F-01 fixed, `6a5d190`) |
| `routing.backends` overrides | ✅ | ⬚ | ✅ | ✅ | `max_concurrency` persists and hot-applies (F-01/F-05 fixed, `6a5d190`) |
| `follow_gateway` strategy adoption | ✅ | ⬚ | ✅ | ✅ | Runtime-only, never persisted |
| Batch sharding (`batch_shard`, HRW/modulo) | ✅ | — | — | — | Header/`user`/`metadata` driven; `shardless_fallbacks` counter surfaces misuse |
| `model_groups` (weights, prefer, batch eligibility) | ⬚ | ⬚ | ⬚ | ⬚ | **Not built** as config. The internal `ModelGroup` representation exists and `model_pools` parses into it, so adding `routing.model_groups` is a second *parser*, not a second mechanism (F-25) |

## Source identity and per-caller routing

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| Source attribution (header → key → UA → default) | ✅ | ✅ (`sources list`) | ✅ | ✅ | |
| `sources toggle <id>` one-click registration | ✅ | ✅ | ✅ | ✅ | Never auto-installs a CLI — deliberate |
| `connect <id>` harness wiring guide | ✅ | ✅ | ⬚ | ⬚ | Copy-paste env + optional `--toggle`; never edits editor configs |
| Harness registry + PATH detection + icons | ✅ | ✅ | ✅ | ✅ | 3 of 6 `cli_commands` entries are unverified guesses (flagged in-code) |
| Per-source `strategy` / `local_only` / `allow_cloud` / `prefer_provider` | ✅ | ⬚ | ✅ | ✅ | |
| Per-source `cloud_providers` allowlist | ✅ | ⬚ | ◐ | ◐ | Rendered generically; no picker |
| Per-source `max_concurrency` (429 on breach) | ✅ | ⬚ | ◐ | ◐ | Atomic admission (F-08 fixed, `3b6ec71`) |
| Per-source `secret` / `secret_env` | ✅ | ⬚ | ✅ | ✅ | Blanked on read by both surfaces |
| Elevated-source secret enforcement | ✅ | ✅ | ✅ | ✅ | Shared guard on every write path (F-02 fixed, `6a5d190`) |
| `sources[].model_rewrites` | ✅ | ⬚ | ✅ | ⬚ | Dashboard renders it generically (`dict_strings` widget); **deliberately excluded** from the macOS renderer (`SettingsWindowView.swift:775`) |
| `sources[].scenarios` (scenario routing) | ✅ | ⬚ | ✅ | ⬚ | Dashboard renders it generically (`dict` + `ScenarioRule` item schema); same documented macOS exclusion |
| `sources[].match.user_agent_contains` | ✅ | ⬚ | ✅ | ⬚ | Dashboard renders it generically (`object` widget); same documented macOS exclusion |
| Scenario classification (long_context / web_search / think / background) | ✅ | ⬚ | ⬚ | ⬚ | Engine-side only; the counters it produces are displayed nowhere — see the observability table |

**How the dashboard gets these for free.** `renderSourcesTab()` calls
`renderSchemaField(byName.sources, …)`, and `schemaListOfObjectsRow` renders **every** field in
the `SourceConfig` item schema through `renderSchemaField`
(`dashboard.js:1361-1400`, `:1511-1531`). So `model_rewrites` → `schemaDictStringsRow`,
`scenarios` → `schemaDictOfObjectsRow`, `match` → `schemaNestedObjectRow` — no per-field JS was
ever written for them. This is the schema-driven UI work of
`config-schema-rewrite-plan.md` paying off, and it is invisible to a name-grep of `dashboard.js`
(searching for the literal string `scenarios` there returns zero hits).

**The real gap is macOS + observability, not configurability.** The macOS Settings app excludes
those three fields on purpose — `SchemaFormView`'s fallback widget is a plain text field bound to
`.stringValue`, which is `nil` for a dict/object, so rendering them would let a user silently
overwrite a structured value with a string. That decision, and the in-app caption pointing users
to the dashboard, are documented in `cli-source-routing-plan.md` Phase 4b. The web
dashboard **Serving** tab shows `source_requests` and `scenario_requests` (from
`GET /netllm/v1/status`) alongside router telemetry; macOS menubar **Serving Stats**
still omits scenario keys. What remains open is **live validation** — whether rules
fire under real Claude Code / Codex traffic (Phase 3 gate, deferred to Phase 5).

## Cloud providers

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| 5-provider registry (Moonshot, Z.ai, OpenAI, Anthropic, OpenRouter) | ✅ | ✅ | ✅ | ✅ | Served from one endpoint; no client-side mirroring of display data |
| Enable/disable per provider | ✅ | ✅ | ✅ | ✅ | Disabling prunes the pool row immediately |
| API key storage (`api_key`, `api_key_env`) | ✅ | ✅ | ✅ | ✅ | Write-only; macOS also has Keychain support |
| Region / profile selection | ✅ | ✅ | ✅ | ✅ | |
| Fallback direction (`cloud` / `local` / `none`) | ✅ | ✅ | ✅ | ✅ | `local` = cloud-primary (`cloud_leads`) |
| OpenRouter OAuth PKCE | ✅ | ✅ | ⬚ | ⬚ | CLI-only (`cloud connect openrouter`) |
| Anthropic `plan_token` mode | ✅ | ◐ | ◐ | ◐ | Unofficial by Anthropic's own docs; opt-in, correctly flagged in-code |
| Live model-catalog probe + allowlist editing | ✅ | ✅ (`cloud test`) | ✅ | ✅ | |
| Keyless-but-enabled detection | ✅ | ✅ | ✅ | ✅ | Row is not materialised; doctor flags it |
| Legacy env/caller-key inject | ✅ | — | — | — | Request-scoped now; never pooled (F-04 fixed, `3b6ec71`) |

## Swarm and operations

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| mDNS advertise + browse | ✅ | ✅ | ✅ | ✅ | Auto-retries after a startup name collision |
| Static peers | ✅ | ✅ | ✅ | ✅ | Self-peer filtering on the HTTP path only |
| Subnet scan (manual + auto-fallback) | ✅ | ✅ | ✅ | ✅ | |
| Heartbeat gossip | ✅ | — | — | — | Bounded concurrent fan-out (F-12 fixed, `15ac9c7`) |
| Peer re-discovery after sleep/blip | ✅ | — | — | — | |
| Cluster token (create / rotate / join) | ✅ | ✅ | ✅ | ✅ | |
| `require_token_for_inference` | ✅ | ✅ | ✅ | ✅ | Set by `init --swarm --secure`; doctor flags the mismatch (F-14 fixed, `15ac9c7`) |
| **Drain (`draining`)** | ✅ | ✅ | ⬚ | ⬚ | **CLI-only.** No button in the dashboard or menubar despite being a pre-restart operation both UIs offer restart for |
| `agent.max_concurrency` (self-declared ceiling) | ✅ | ⬚ | ✅ | ✅ | Broadcast via heartbeat |
| Peer config/version drift warnings | ✅ | ✅ | ✅ | ◐ | In `status.peer_warnings` and doctor |
| Gateway role promotion | ✅ | ✅ | ✅ | ✅ | |

## Observability

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| Prometheus `/metrics` (7 collectors) | ✅ | — | — | — | Requests, latency, health, in-flight, source, scenario, token counters |
| Router session + all-time telemetry | ✅ | ⬚ | ✅ | ✅ | Debounced persistence (F-09 fixed, `3b6ec71`). `docs/telemetry-api.md` is normative and the emitted key set is gated by `tests/contract/test_telemetry_contract.py`; clients read `total_tokens`, never re-derive it (F-49 contract slice fixed) |
| oMLX deep telemetry (stats, activity, loaded models) | ✅ | ⬚ | ✅ | ✅ | Only provider with this depth |
| Host CPU/memory block | ✅ | — | ✅ | ✅ | `psutil` is a declared dependency (F-10 fixed, `bb3eae0`) |
| Per-backend `routed_requests` counters | ✅ | ✅ | ✅ | ◐ | Answers "peer discovered but idle" |
| `capacity_rejections` counters | ✅ | ⬚ | ✅ | ⬚ | |
| `shardless_fallbacks` counter | ✅ | ⬚ | ✅ | ⬚ | |
| **`source_requests` / `scenario_requests` counters** | ✅ | ⬚ | ⬚ | ⬚ | Web **Serving** tab (status poll); Prometheus + `/metrics`; macOS menubar omits scenario keys |
| Agent log tail | ✅ | ⬚ | ✅ | ✅ | Rotates at 10 MB × 3 (F-15 fixed, `15ac9c7`) |
| Doctor | ✅ | ✅ (full) | ◐ (subset) | ◐ (subset) | |

## Platform and packaging

| Capability | macOS | Linux | Windows |
|-----------|-------|-------|---------|
| CLI + foreground `serve` | ✅ | ✅ | ✅ |
| Background service | ✅ menubar app / `brew services` | ✅ systemd `--user` | ✅ `NetllmAgent` service |
| Native GUI | ✅ SwiftUI menubar | ⬚ | ⬚ |
| Web dashboard `/ui/` | ✅ | ✅ | ✅ |
| In-app updater | ✅ | ⬚ (package manager) | ⬚ (re-download zip) |
| Signed/notarized artifact | ❌ ad-hoc only | ⬚ unsigned | ⬚ unsigned |
| oMLX provider support | ✅ | — | — |
| CPU architecture | **arm64 only** | x86_64 (deb/rpm) | x64 |

**macOS 26 (Tahoe) blocker:** ad-hoc-signed DMGs are refused by Gatekeeper. The documented
workaround is build-from-source. `docs/macos-code-signing.md` contains the full enablement
procedure — this is a credentials and release-process task, not engineering work.

## Roadmap-visible gaps (from the project's own plan docs, verified against code)

| Plan | Stated status | Verified reality |
|------|--------------|------------------|
| `routing-hardening-plan.md` Phases 1–3, 5 | done | ✅ genuinely implemented |
| `routing-hardening-plan.md` Phase 4 — `model_groups` | "still future work" | ✅ accurate as *config*; since the F-25 consolidation `model_pools` parses into the internal `ModelGroup`, so the "fold, don't coexist" requirement is already satisfied at the data-model level |
| `routing-hardening-plan.md` — `require_same_model_for_shard` | Phase 1 says "now actually wired in"; Phase 5 says the planner was deleted and the field "is a no-op again" | ⚠️ **self-contradictory** — the Phase 1 bullet was never struck through. `config.example.toml` and `packages/netllm-core/AGENTS.md` both correctly say deprecated. Fixed 2026-07-29 (F-17) |
| `config-schema-rewrite-plan.md` Phases 1–5 | "done, with two scope limits" | ✅ accurate; the limits are real and still cost (F-21) |
| `models-ux-plan.md` A + B1–B3 | delivered (macOS) | ✅ |
| `models-ux-plan.md` B4, C | deferred | ✅ accurate |
| `models-ux-plan.md` D — dashboard parity | not started | ✅ accurate |
| `cloud-providers-plan.md` | "proposed" | ❌ **stale**: all 7 phases are implemented across CLI, dashboard, and macOS app. Fixed 2026-07-29 |
| `cli-source-routing-plan.md` Phases 0–5 | per-phase "(done)" markers, no top-level status | ✅ accurate per phase, including the Phase 4b macOS exclusion rationale. Two deferrals it records are still open: the `x-netllm-scenario` response header, and Phase 3's live-validation gate. Top-level status line added 2026-07-29 |

## Suggested product decisions

| # | Decision needed | Why now |
|---|-----------------|---------|
| 1 | **Live scenario-routing validation** | Dashboard Serving tab now surfaces `source_requests` / `scenario_requests`; Phase 3's live-validation gate (real Claude Code / Codex sessions) remains open. |
| 2 | ~~Decide whether the LAN swarm default is "open" or "secured"~~ | **Decided and done** (`15ac9c7`) — a cluster token now gates reads *and* inference; `--secure` sets both. Open trusted-LAN (no token) is unchanged. |
| 3 | ~~Retire the caller-key cloud inject~~ | **Done** (`3b6ec71`) — request-scoped, never pooled. The registry path is now the only pooled cloud mechanism. |
| 4 | **Surface drain in the dashboard and menubar** | Both offer "Restart Agent"; drain is the safe pre-restart step and only the CLI has it. |
| 5 | ~~Declare `psutil` or drop host metrics~~ | **Done** (`bb3eae0`) — declared; the block now populates. |
| 6 | **Fund notarization** | The macOS DMG channel is effectively unusable on current macOS without it. |
| 7 | **Refresh the three stale plan docs** | `cloud-providers-plan.md` (proposed → shipped), `routing-hardening-plan.md` (`require_same_model_for_shard`), and add a status line to `cli-source-routing-plan.md`. |
