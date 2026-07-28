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

## Routing engine

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| 8 routing strategies | ✅ | ✅ | ✅ | ✅ | |
| Per-request headers (`x-netllm-strategy`, `-backend`, `-local-only`, `-hops`) | ✅ | — | — | — | Protocol-level; documented in `routing-hardening-plan.md` |
| Failover with per-request exclusion | ✅ | — | — | — | |
| Capacity-error classification (409/429/503/507 + body markers) | ✅ | — | — | — | Surfaced as `capacity_rejections` in status |
| `routing.model_aliases` | ✅ | ⬚ | ✅ | ✅ | No CLI command; edit via config or a UI |
| `routing.model_pools` | ✅ | ⬚ | ✅ | ✅ | Config-only when the plan was written; both UIs now render it |
| `routing.policies` | ✅ | ⬚ | ✅ | ✅ | **`source` field silently dropped on save — F-01** |
| `routing.backends` overrides | ✅ | ⬚ | ✅ | ✅ | **`max_concurrency` dropped on save and never hot-applied — F-01, F-05** |
| `follow_gateway` strategy adoption | ✅ | ⬚ | ✅ | ✅ | Runtime-only, never persisted |
| Batch sharding (`batch_shard`, HRW/modulo) | ✅ | — | — | — | Header/`user`/`metadata` driven; `shardless_fallbacks` counter surfaces misuse |
| `model_groups` (weights, prefer, batch eligibility) | ⬚ | ⬚ | ⬚ | ⬚ | **Not built.** Sketched in `routing-hardening-plan.md` Phase 4 |

## Source identity and per-caller routing

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| Source attribution (header → key → UA → default) | ✅ | ✅ (`sources list`) | ✅ | ✅ | |
| `sources toggle <id>` one-click registration | ✅ | ✅ | ✅ | ✅ | Never auto-installs a CLI — deliberate |
| Harness registry + PATH detection + icons | ✅ | ✅ | ✅ | ✅ | 3 of 6 `cli_commands` entries are unverified guesses (flagged in-code) |
| Per-source `strategy` / `local_only` / `allow_cloud` / `prefer_provider` | ✅ | ⬚ | ✅ | ✅ | |
| Per-source `cloud_providers` allowlist | ✅ | ⬚ | ◐ | ◐ | Rendered generically; no picker |
| Per-source `max_concurrency` (429 on breach) | ✅ | ⬚ | ◐ | ◐ | **Cap is check-then-act — F-08** |
| Per-source `secret` / `secret_env` | ✅ | ⬚ | ✅ | ✅ | Blanked on read by both surfaces |
| Elevated-source secret enforcement | ◐ | ⬚ | ✅ | ❌ | **Bypassed on the macOS/CLI save path — F-02** |
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
to the dashboard, are documented in `cli-source-routing-plan.md` Phase 4b. What is genuinely
missing is **the feedback loop**: nothing on any surface displays `scenario_requests` or
`source_requests`, so a user who writes a scenario rule in the dashboard has no way to see
whether it ever fires. The plan itself records this — Phase 3's live-validation gate was
deferred to Phase 5 and never closed.

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
| Legacy env/caller-key inject | ✅ | — | — | — | **Should be retired — F-04, F-25** |

## Swarm and operations

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| mDNS advertise + browse | ✅ | ✅ | ✅ | ✅ | Auto-retries after a startup name collision |
| Static peers | ✅ | ✅ | ✅ | ✅ | Self-peer filtering on the HTTP path only |
| Subnet scan (manual + auto-fallback) | ✅ | ✅ | ✅ | ✅ | |
| Heartbeat gossip | ✅ | — | — | — | **Sequential fan-out — F-12** |
| Peer re-discovery after sleep/blip | ✅ | — | — | — | |
| Cluster token (create / rotate / join) | ✅ | ✅ | ✅ | ✅ | |
| `require_token_for_inference` | ✅ | ⬚ | ✅ | ✅ | **No CLI command; not set by `--secure` — F-14** |
| **Drain (`draining`)** | ✅ | ✅ | ⬚ | ⬚ | **CLI-only.** No button in the dashboard or menubar despite being a pre-restart operation both UIs offer restart for |
| `agent.max_concurrency` (self-declared ceiling) | ✅ | ⬚ | ✅ | ✅ | Broadcast via heartbeat |
| Peer config/version drift warnings | ✅ | ✅ | ✅ | ◐ | In `status.peer_warnings` and doctor |
| Gateway role promotion | ✅ | ✅ | ✅ | ✅ | |

## Observability

| Feature | Engine | CLI | Dashboard | macOS app | Notes |
|---------|--------|-----|-----------|-----------|-------|
| Prometheus `/metrics` (7 collectors) | ✅ | — | — | — | Requests, latency, health, in-flight, source, scenario, token counters |
| Router session + all-time telemetry | ✅ | ⬚ | ✅ | ✅ | **Disk write per request — F-09** |
| oMLX deep telemetry (stats, activity, loaded models) | ✅ | ⬚ | ✅ | ✅ | Only provider with this depth |
| **Host CPU/memory block** | ◐ | — | ❌ | ✅ | **Always `null` in the API — `psutil` undeclared (F-10).** macOS has native `HostSampler`; Linux/Windows dashboards show nothing |
| Per-backend `routed_requests` counters | ✅ | ✅ | ✅ | ◐ | Answers "peer discovered but idle" |
| `capacity_rejections` counters | ✅ | ⬚ | ✅ | ⬚ | |
| `shardless_fallbacks` counter | ✅ | ⬚ | ✅ | ⬚ | |
| **`source_requests` / `scenario_requests` counters** | ✅ | ⬚ | ⬚ | ⬚ | In status + Prometheus, **no UI** |
| Agent log tail | ✅ | ⬚ | ✅ | ✅ | **Log never rotates — F-15** |
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
| `routing-hardening-plan.md` Phase 4 — `model_groups` | "still future work" | ✅ accurate; `model_pools` shipped as the simpler half |
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
| 1 | **Close the scenario-routing feedback loop** | Rules are configurable in the dashboard, but `scenario_requests` / `source_requests` are displayed nowhere, so nobody can tell whether a rule fires. Phase 3's own validation gate is still open. A counters panel is small work with high payoff on the product's most differentiated capability. |
| 2 | **Decide whether the LAN swarm default is "open" or "secured"** | `--secure` does not secure inference (F-14). Pick one and make the flag mean it. |
| 3 | **Retire the caller-key cloud inject** | It is a cross-tenant credential path (F-04) and a duplicate of the registry mechanism. |
| 4 | **Surface drain in the dashboard and menubar** | Both offer "Restart Agent"; drain is the safe pre-restart step and only the CLI has it. |
| 5 | **Declare `psutil` or drop host metrics** | The feature ships dead on Linux/Windows (F-10). |
| 6 | **Fund notarization** | The macOS DMG channel is effectively unusable on current macOS without it. |
| 7 | **Refresh the three stale plan docs** | `cloud-providers-plan.md` (proposed → shipped), `routing-hardening-plan.md` (`require_same_model_for_shard`), and add a status line to `cli-source-routing-plan.md`. |
