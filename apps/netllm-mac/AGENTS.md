# netllm-mac — macOS menubar app

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Swift menubar application that supervises the netllm Python agent, exposes settings/welcome/updater UI, and embeds venvstacks Python layers from packaging export.

## Ownership

| Path | Role |
|------|------|
| `Sources/App/` | Entry, delegate, lifecycle |
| `Sources/Menubar/` | Status item, stats polling |
| `Sources/Server/` | Process supervisor, control socket |
| `Sources/Config/` | TOML slices, CLI shim, `AgentAPI` HTTP client, branding, tokens |
| `Sources/AppView/` | Settings (`SettingsViewModel` live poll), welcome, about, glass chrome |
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
- **Settings live status:** `SettingsViewModel` polls `/health` + `/netllm/v1/status` every 2s while Settings is open; **Restart Agent** waits for `/health` before refreshing stats (avoids stale "waiting for HTTP health" / backends `—`)
- **LAN swarm QoL:** welcome **Listen on LAN** sets `swarm.subnet_scan = true` + `local_spillover` (open trusted LAN, no token). Settings **Require cluster token** toggle mints token on save and offers **Copy join command** for secured pairing. Settings auto-runs `POST /netllm/v1/admin/peers-scan` once per session when agent is healthy (display only; runtime merge is agent-side). Manual **Scan & save** still persists `swarm.peers` when mDNS is blocked.
- **HTTP client host:** Settings and menubar **Open Dashboard** use `127.0.0.1` (`AppConfig.connectableHost`); web UI opened at `http://<LAN-IP>:11400/ui/` on the same Mac is equivalent after agent admin-host fix ([netllm-agent/AGENTS.md](../../packages/netllm-agent/AGENTS.md))
- **Cloud settings** (`CloudSettingsView.swift`, Settings → Cloud sidebar row): `document.cloud` (enable/fallback/per-provider enable+region+api_format) round-trips through `netllm config export/import` like every other section; API keys are **not** in `NetllmConfigDocument` — they live in Keychain only (`KeychainStore.Account.{anthropic,openai,moonshot,zai,openrouter}APIKey`, resolved by id via `KeychainStore.accountForCloudProvider`), injected into the agent subprocess env by `PythonRuntime.injectCloudAPIKeys` using each provider's `api_key_env` name from the Python registry (`netllm_core.cloud_providers`) — restart the agent to pick up a changed key.
- **Cloud provider display metadata is server-fetched, not hardcoded**: `SettingsViewModel.cloudProviders` (computed) prefers the live `cloudProviderRegistry` (populated once per session in `refreshLiveData()` via `AgentAPI.cloudProviderRegistry` → `GET /netllm/v1/cloud/providers`) and falls back to `SettingsViewModel.cloudProvidersBootstrap` only when the agent is unreachable. Always read `model.cloudProviders` in views — never the static bootstrap list directly.
- **Models tab & pool pickers** (docs/models-ux-plan.md phases A + B1–B3): `ModelsTabView.swift` renders one machine-grouped, collapsible, searchable list from live `status.backends` (grouped via `BackendStatus.agentId`/`backendId`, parsed in `AgentAPI.parseBackend`); rows carry `routing.model_pools` membership badges (green/orange effectiveness dot computed client-side by `SettingsViewModel.poolInactiveReason` mirroring `pool.py _backend_matches_host_ref` — keep in sync) and an add/remove-pool menu writing the **same** `document.routing.model_pools` draft the Routing tab binds to. Routing's pool editor threads `SettingsViewModel.knownHostRefs`/`knownModelIDs` as `SchemaFieldOverride.suggestions` into `EditableStringList` (picker menu + soft unknown-value warning; free text stays allowed — offline hosts are legitimate). Models-tab filter/collapse state lives on the view model, not `@State` — the detail view's `.id(uiRevision)` resets view-local state every 2s poll. Per-model request metrics are deliberately absent (plan phase C: server doesn't track them; don't fake from backend counters).
- **`document.ui`/`.discovery`/`.swarm` are `[String: JSONValue]`, not typed structs** (docs/config-schema-rewrite-plan.md §5 phase 4, Option A) — `JSONValue.swift`'s `Binding<[String: JSONValue]>` extensions (`.string()`/`.bool()`/`.double()`/`.stringArray()`/`.stringArray(_:subKey:)`) bridge them back to plain Swift types for existing views. `SchemaFormView`/`SchemaFieldOverride` (`Sources/AppView/SchemaFormView.swift`) render generically from `ConfigStore.loadSchema()` (`netllm config schema`) where a section has no hand-tuned view (`ui`, the 3 new swarm fields, `routing.model_pools`). `routing`'s other fields and all of `cloud` are still typed structs — deliberate, not partial-migration debt; see the plan doc before "finishing" that migration.

## Work Guidance

- Build: `uv sync`, `uv pip install venvstacks`, `apps/netllm-mac/Scripts/build.sh release`
- Validate updater/install with `tests/test_bundled_install_scripts.sh` before release tags
- Commit macOS install/update fixes as focused slices separate from unrelated work

## Verification

```bash
apps/netllm-mac/Scripts/build.sh release
scripts/verify-before-pr.sh
scripts/test-menubar-e2e.sh
tests/test_bundled_install_scripts.sh
```

User docs: [../../docs/macos-install.md](../../docs/macos-install.md), [../../docs/macos-troubleshooting.md](../../docs/macos-troubleshooting.md).

## Child DOX Index

None — Swift sources grouped under `Sources/` by concern; no nested AGENTS.md until a subtree gains independent release or ownership.
