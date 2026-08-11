# netllm-mac — macOS menubar app

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Swift menubar application that supervises the netllm Python agent, exposes settings/welcome/updater UI, and embeds venvstacks Python layers from packaging export.

## Ownership

| Path | Role |
|------|------|
| `Sources/App/` | Entry, delegate, lifecycle |
| `Sources/Menubar/` | Menubar popover + AppKit menu fallback (`MenubarController`, `MenubarStatusFormatter`), telemetry poller, System Stats fly-out, Serving Stats submenu, optional gauge status items |
| `Sources/Server/` | Process supervisor, control socket |
| `Sources/Config/` | TOML slices, CLI shim, `AgentAPI` HTTP client, `AgentHTTP` URL helper, branding, tokens |
| `Sources/AppView/` | Settings (`SettingsWindowView`, `HomeTabView`, `IntegrationsTabView`, `PreferencesTabView`; `SettingsViewModel` live poll), welcome, about, glass chrome |
| `Sources/Updater/` | GitHub Releases check, in-app install |
| `Sources/Welcome/` | First-run wizard |
| `Scripts/build.sh` | Release/stage build (venvstacks + Swift); ad-hoc sign unless `CODESIGN_IDENTITY` set |
| `design-tokens.json` | Dashboard token source (sync via `scripts/generate-dashboard-tokens.py`) |

## Local Contracts

- `Package.swift`: swift-tools **5.9** (CI runs Swift 5.10 on macos-14)
- SwiftUI views: `@MainActor`; gate Tahoe `glassEffect` behind `LIQUID_GLASS_SDK` in `build.sh`
- In-app update must stop agent and free `:11400` — no manual **Stop** required first
- Repo checkout does not update `/Applications/llm-swarm-router.app`; user upgrade: menubar **Updates** or bundled `macos-app-install.sh` (embedded under `Contents/Resources/Scripts/`); `scripts/upgrade-mac-app.sh` is repo-maintainer wrapper only
- Logs: `~/Library/Application Support/netllm/logs/`
- **Gatekeeper:** ad-hoc Stage/DMG builds do not launch on macOS 26+; release path is Developer ID + notarize via [packaging/scripts/local-notarized-dmg.sh](../../packaging/scripts/local-notarized-dmg.sh) or CI ([macos-code-signing.md](../../docs/macos-code-signing.md))
- **Settings live status:** `SettingsViewModel` polls `/health` + cache-fast `/netllm/v1/status` every 2s while Settings is open; toolbar **Refresh** calls status with `?scan=1&probe=1`. Pool editor pickers also merge `/v1/models` into model suggestions. **Restart Agent** waits for `/health` before refreshing stats (avoids stale "waiting for HTTP health" / backends `—`)
- **LAN swarm QoL:** welcome **Listen on LAN** sets `swarm.subnet_scan = true` + `local_spillover` (open trusted LAN, no token). Settings **Require cluster token** toggle mints token on save and offers **Copy join command** for secured pairing. Settings auto-runs `POST /netllm/v1/admin/peers-scan` once per session when agent is healthy (display only; runtime merge is agent-side). Manual **Scan & save** still persists `swarm.peers` when mDNS is blocked.
- **HTTP client host:** Settings and menubar **Open Dashboard** use `127.0.0.1` (`AppConfig.connectableHost`); web UI opened at `http://<LAN-IP>:11400/ui/` on the same Mac is equivalent after agent admin-host fix ([netllm-agent/AGENTS.md](../../packages/netllm-agent/AGENTS.md))
- **Cloud settings** (`CloudSettingsView.swift`, Settings → Cloud sidebar row): `document.cloud` (enable/fallback/per-provider enable+region+api_format) round-trips through `netllm config export/import` like every other section; API keys are **not** in `NetllmConfigDocument` — they live in Keychain only (`KeychainStore.Account.{anthropic,openai,moonshot,zai,openrouter}APIKey`, resolved by id via `KeychainStore.accountForCloudProvider`), injected into the agent subprocess env by `PythonRuntime.injectCloudAPIKeys` using each provider's `api_key_env` name from the Python registry (`netllm_core.cloud_providers`) — restart the agent to pick up a changed key.
- **Cloud tab drafts live on the view model, not @State**: the Settings detail view's `.id(uiRevision)` recreates the whole tree every 2-second live poll, which destroyed `CloudProviderCard`'s `@State` key text mid-typing (the "API key disappears on save/Enter" bug). Key drafts/feedback/catalogs are keyed by provider id on `SettingsViewModel` (`cloudKeyDrafts` etc.); Keychain is read once per provider per session (`loadCloudKeyDraftIfNeeded`) — also avoids one ad-hoc-signing Keychain prompt per poll. Any future per-row editable state in Settings must follow this pattern (same reason `modelsSearchText`/`modelsCollapsedGroups` live there).
- **Cloud model allowlist UI** (Cloud tab per-provider "Models" section): fetches the provider's full catalog via `AgentAPI.cloudProviderModels` → `GET /netllm/v1/cloud/providers/{id}/models`, renders checkboxes bound to `document.cloud.providers[id].models` (empty allowlist = all models, matching server materialization; first uncheck materializes catalog-minus-one; "Enable all" resets to empty). Applies after Save + Restart Agent. Cloud backends group as "<Display name> (cloud)" on the Models tab (`BackendStatus.cloudProvider`), where the existing pool menu covers cloud models.
- **Cloud provider display metadata is server-fetched, not hardcoded**: `SettingsViewModel.cloudProviders` (computed) prefers the live `cloudProviderRegistry` (populated once per session in `refreshLiveData()` via `AgentAPI.cloudProviderRegistry` → `GET /netllm/v1/cloud/providers`) and falls back to `SettingsViewModel.cloudProvidersBootstrap` only when the agent is unreachable. Always read `model.cloudProviders` in views — never the static bootstrap list directly.
- **Models tab & pool pickers** (docs/models-ux-plan.md phases A + B1–B3): `ModelsTabView.swift` renders one machine-grouped, collapsible, searchable list from live `status.backends` (grouped via `BackendStatus.agentId`/`backendId`, parsed in `AgentAPI.parseBackend`); rows carry `routing.model_pools` membership badges (green/orange effectiveness dot computed client-side by `SettingsViewModel.poolInactiveReason` mirroring `ModelResolver._host_matches` in `pool.py` — keep dashboard, macOS, and core in sync) and an add/remove-pool menu writing the **same** `document.routing.model_pools` draft the Routing tab binds to. Routing's pool editor threads `SettingsViewModel.knownHostRefs`/`knownModelIDs` as `SchemaFieldOverride.suggestions` into `EditableStringList` (picker menu + soft unknown-value warning; free text stays allowed — offline hosts are legitimate). Models-tab filter/collapse state lives on the view model, not `@State` — the detail view's `.id(uiRevision)` resets view-local state every 2s poll. Per-model request metrics are deliberately absent (plan phase C: server doesn't track them; don't fake from backend counters).
- **`document.ui`/`.discovery`/`.swarm` are `[String: JSONValue]`, not typed structs** (docs/config-schema-rewrite-plan.md §5 phase 4, Option A) — `JSONValue.swift`'s `Binding<[String: JSONValue]>` extensions (`.string()`/`.bool()`/`.double()`/`.stringArray()`/`.stringArray(_:subKey:)`) bridge them back to plain Swift types for existing views. `SchemaFormView`/`SchemaFieldOverride` (`Sources/AppView/SchemaFormView.swift`) render generically from `ConfigStore.loadSchema()` (`netllm config schema`) where a section has no hand-tuned view (`ui`, the 3 new swarm fields, `routing.model_pools`). `routing`'s other fields and all of `cloud` are still typed structs — deliberate, not partial-migration debt; see the plan doc before "finishing" that migration.
- **Menubar agent status:** `MenubarAppModel.statusTitle` reads live `server.state` (not cached `serverState`); the state observer applies synchronously on the main thread — header text ("Agent stopped" vs running) must stay aligned with Start/Stop menu items (`server.isRunning`)
- **Settings agent status:** `AgentSupervisor.statusLabel` reads live `server.state.settingsStatusLabel` (not the notification-cached `state` copy) — same adopt/restart race class as menubar PR #43; Swift unit tests in `NetllmMacTests.AgentSupervisorStatusLabelTests`; control socket `status`/`start` responses expose `settingsStatusLabel` for `scripts/test-menubar-lifecycle.sh` L5b
- **Menubar UX:** Left-click opens SwiftUI `MenubarPopoverView` (`NSPopover`); right-click falls back to the AppKit menu via `NSMenu.popUp` (not deprecated `NSStatusItem.popUpMenu`). Rich status header (Serving/Draining, role, strategy, backends/peers); **Drain / Resume**, **Restart Agent**, **Copy client env**, expanded **Serving Stats** submenu (`ServingStatsMenuBuilder`: live req/s, source/scenario counts, capacity rejections, windowed backend share). Polls `GET /netllm/v1/telemetry?watch=1` while open; agent HTTP URLs use `AgentHTTP.url(base:path:)` (never `appendingPathComponent` for query strings). **System Stats** uses native `HostSampler`; optional CPU/GPU/MEM/LIV gauges under Settings → Preferences → Appearance (`ui.menubar_*`).
- **Settings IA (web-aligned):** Sidebar groups **Mesh** (Home, Backends, Models & pools, Peers), **Config** (Network rail: Agent/Discovery/Swarm, Routing, Cloud, Preferences), **Tools** (Integrations, Logs, Doctor). **Home** tab merges Status + Serving (throughput, source/scenario counters, drain pill, join commands). **Integrations** tab: client wiring, copy env, write `~/.zshrc`, full sources editor. **Preferences** (was UI): appearance, behaviour, updates, reveal log directory.

## Extension contract

**This package consumes registries. It owns none.** Every provider, surface,
control and harness fact is stated in Python and served over HTTP; the Swift
copies exist so the app renders before it has ever reached an agent, and they
are fallbacks, never sources of truth.

- **Consumes:** `GET /netllm/v1/cloud/providers`,
  `GET /netllm/v1/local-providers`, `GET /netllm/v1/harnesses`,
  `GET /netllm/v1/config/schema` (which also carries `controls`). Always read
  the live registry (`model.cloudProviders`), never the static bootstrap
  list directly.
- **Generated here, never hand-edited:** `KeychainStore.bootstrapProviderIDs`
  sits between `netllm:generated:begin/end:cloud-provider-ids` markers and is
  written by `scripts/generate-registry-artifacts.py`, with `--check` in
  `./scripts/ci.sh lint`.
- **Hand-written offline rosters, projection-tested against the Python
  registry** — these are the two macOS *companions* a new provider needs, and
  they are hand-written because [PROGRAM.md](../../docs/extending/PROGRAM.md)
  §6.3 refuses to generate SwiftUI, not because nobody got round to it:
  - `SettingsViewModel.localProviderBootstrap` — label + first scan port.
    Guard: `tests/conformance/kit_local.py::test_swift_bootstrap_matches_the_registry`.
  - `SettingsViewModel.cloudProvidersBootstrap` — display name, notes,
    regions, auth modes. Guard:
    `tests/conformance/kit_cloud.py::test_settings_bootstrap_covers_every_provider`.
- **Never re-hardcode a derived table.** `PythonRuntime.injectCloudAPIKeys`
  derives every `*_API_KEY` name from the served registry;
  `tests/conformance/kit_cloud.py::test_no_literal_api_key_table_survives_in_pythonruntime`
  fails if a literal comes back. That table was the repo's only silent,
  load-bearing hardcode: a provider added everywhere else stored a key that
  was never exported, so it 401'd against a credential the UI showed as saved.

### Debt: the typed-struct mirrors

`Sources/Config/NetllmConfigDocument.swift` declares typed Swift structs for
`AgentSection`, `RoutingPolicy`, `RoutingSection`, `BackendOverride`,
`CloudProviderConfig` and `CloudSection` that mirror pydantic models in
`netllm_core.models`. **This is acknowledged debt.**

Why it is dangerous rather than merely redundant: `config_merge` rebuilds
identityless row types (`RoutingPolicy`) from the model's defaults plus
whatever the patch sends, so a field the Swift struct does **not** declare is
**erased on Save**, not left alone. That is how `RoutingPolicy.source` was
lost, silently widening a source-scoped policy to every caller. It was found
by an adversarial audit rather than by CI, twice.

- **Guard today:** `tests/conformance/kit_config_surfaces.py` parses these
  structs from source and compares them against the pydantic models. A missing
  field fails by name; the only escape is a dated `[[row_field]]` row in
  `tests/conformance/ledgers/control-parity.toml`.
- **Removal target:** the sections already migrated to
  `[String: JSONValue]` + `SchemaFormView` (`ui`, `discovery`, `swarm`) show
  the shape. `routing`'s remaining fields and all of `cloud` follow, gated on
  `SchemaFormView` growing the widgets the ledger rows name — those rows are
  the tracking list. Read [docs/config-schema-rewrite-plan.md](../../docs/config-schema-rewrite-plan.md)
  before "finishing" that migration; the partial state is deliberate.
- **Cite these structs by name, never by line number.**
  [PROGRAM.md](../../docs/extending/PROGRAM.md) §8 cites them at
  `NetllmConfigDocument.swift:28-35,49-88,99-119,122-150` and every one of
  those ranges is already stale.

## Work Guidance

- Build: `uv sync`, `uv pip install venvstacks`, `apps/netllm-mac/Scripts/build.sh release`
- Validate updater/install with `tests/test_bundled_install_scripts.sh` before release tags
- Commit macOS install/update fixes as focused slices separate from unrelated work

## Verification

```bash
cd apps/netllm-mac && swift build -c release && swift test
apps/netllm-mac/Scripts/build.sh release
scripts/verify-before-pr.sh
scripts/test-menubar-e2e.sh
tests/test_bundled_install_scripts.sh
```

User docs: [../../docs/macos-install.md](../../docs/macos-install.md), [../../docs/macos-troubleshooting.md](../../docs/macos-troubleshooting.md).

## Child DOX Index

None — Swift sources grouped under `Sources/` by concern; no nested AGENTS.md until a subtree gains independent release or ownership.

Updated: 2026-08-11 (menubar popover + Settings Home/Network/Integrations/Preferences IA)
