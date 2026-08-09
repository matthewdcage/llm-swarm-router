# Extension-cost inventory: what it actually costs to add one X today

Measured against `main @ 243e3dc`, using real commits and real files. Where a "recent example" exists as a landed commit I diffed it rather than estimating.

---

## Headline

| Axis | Files touched (real example) | Duplicated facts | Enforcement | Verdict |
|---|---|---|---|---|
| **A. Cloud provider** | **13** (measured, commit `08946b6`) | 8 fact-classes across Python/JS/Swift/TOML/5 docs | 3 roster tests trip; Swift + docs unguarded | **Medium friction, one sharp edge** |
| **B. Local backend provider** | **~14** (no recent example exists — reconstructed) | **11 parallel maps keyed on the same provider id**, 4 of them env-var maps | **Zero tests reference `KNOWN_PROVIDERS`** | **WORST — highest duplication, lowest enforcement** |
| **C. API surface** | **1–3** for a dialect bridge (`/v1/responses`); ~11 for a true new `Surface` | 4 out-of-adapter `Surface` branches + 2 literal mirrors | Real AST gate (`check-engine-erosion.py`) in `ci.sh lint` | **Cleanest axis by a wide margin** |
| **D. CLI command / control-plane setting** | **32** (measured, commit `bf67238`) | 3-surface parity is manual; 4× duplicated skill files | Per-surface tests exist; **no cross-surface parity test** | **High volume, but mostly genuine work** |
| **E. Config schema evolution** | 1–6 depending on section | 2 hand-maintained field allowlists mirroring pydantic models | Strong schema derivation tests; **no version, no migration, no deprecation** | **Structurally missing: no versioning at all** |

---

## Axis A — Add a cloud provider

**Real example on disk:** `08946b6 feat(cloud): add Alibaba Cloud DashScope (Qwen) provider`, on `remotes/origin/feat/dashscope-cloud-provider` (not merged to main). This is the exact experiment the task asked for.

`git show --stat 08946b6` → **13 files, +127/−13**:

```
AGENTS.md                                                   |  2 +-
apps/netllm-mac/Sources/AppView/SettingsViewModel.swift     |  7 +++
apps/netllm-mac/Sources/Config/KeychainStore.swift          |  2 +
apps/netllm-mac/Sources/Server/PythonRuntime.swift          |  1 +
config.example.toml                                         |  9 +++-
docs/editor-integration.md                                  |  4 +-
packages/netllm-agent/.../static/dashboard.js               |  1 +
packages/netllm-core/AGENTS.md                              |  2 +-
packages/netllm-core/src/netllm_core/cloud_providers.py     | 53 +++++++-
packages/netllm-core/src/netllm_core/models.py              |  4 +-
tests/test_admin_cloud.py                                   | 18 +++-
tests/test_cloud_providers.py                               | 28 +++-
tests/test_dashboard_cloud_tab.py                           |  9 +-
```

Only **1 of those 13** (`cloud_providers.py`) is the actual new fact. The other 12 are mirrors, roster edits, and doc prose.

### Duplicated-fact list (file:line, main unless noted)

| Fact | Locations |
|---|---|
| provider id | `packages/netllm-core/src/netllm_core/cloud_providers.py:16` (Literal), `:49` (dict key), `:50` (`id=`), `packages/netllm-agent/src/netllm_agent/static/dashboard.js:12-18`, `apps/netllm-mac/Sources/AppView/SettingsViewModel.swift:103-140`, `apps/netllm-mac/Sources/Config/KeychainStore.swift:24-29` (switch), `config.example.toml:250` |
| `display_name` | `cloud_providers.py:51`, `SettingsViewModel.swift:106`, plus 5 doc rosters |
| region keys | `cloud_providers.py:52-62` (endpoints dict), `SettingsViewModel.swift:108` (`regions:`), `config.example.toml:252` |
| `notes` prose | `cloud_providers.py:66-71`, `SettingsViewModel.swift:107` |
| **`api_key_env`** | `cloud_providers.py:64`, **`apps/netllm-mac/Sources/Server/PythonRuntime.swift:79-85`**, `config.example.toml` |
| Keychain account | `KeychainStore.swift:9-14` (constants) + `:24-29` (switch) |
| roster assertion sets | `tests/test_cloud_providers.py:25`, `tests/test_admin_cloud.py:32`, `tests/test_admin_cloud.py:196`, `tests/test_dashboard_cloud_tab.py:46` |
| doc rosters | `AGENTS.md:71`, `packages/netllm-core/AGENTS.md:37`, `docs/editor-integration.md:222`, `docs/architecture/06-dependencies.md:141-152` (table), `docs/architecture/05-configuration-and-control-plane.md:26` (mermaid), `docs/cloud-providers-plan.md:65,111,127` |

### What is already clean here (credit where due)

The server-is-truth design mostly works. `admin.cloud_provider_registry_payload()` (`packages/netllm-agent/src/netllm_agent/admin.py:215-235`) serves `id / display_name / notes / regions / auth_modes / default_api_format / api_key_env` from the Python registry, and both clients prefer it:
- `dashboard.js:8-11` — comment states the bootstrap list is offline-fallback only.
- `SettingsViewModel.swift:145-148` — `cloudProviders` returns the live registry when non-empty.
- `KeychainStore.accountForCloudProvider` (`KeychainStore.swift:20-31`) has `default: return "\(providerId)_api_key"`, so an unknown provider still gets a Keychain slot.
- `AgentAPI.swift:121` resolves `keychainAccount` generically from the live registry.

So `dashboard.js`, `SettingsViewModel.swift`, and `KeychainStore.swift` are all **degraded-mode-only** mirrors. Forgetting them costs you a wrong list for the ~200 ms before the first fetch. Low blast radius.

### The one sharp edge — silent, load-bearing, untested

`apps/netllm-mac/Sources/Server/PythonRuntime.swift:79-85` is a **closed** `[(account, envVar)]` list:

```swift
let keychainToEnvVar: [(account: String, envVar: String)] = [
    (KeychainStore.Account.anthropicAPIKey, "ANTHROPIC_API_KEY"),
    ... (KeychainStore.Account.openrouterAPIKey, "OPENROUTER_API_KEY"),
]
```

Forget this one line and the failure is invisible: the macOS Settings Cloud tab renders the new provider (live registry), accepts the key, writes it to Keychain under `<id>_api_key` (generic default) — and the key is **never injected into the agent subprocess env**. The provider silently 401s with a key the user can see stored in the UI.

This is gratuitous: `api_key_env` is already on the wire at `admin.py:232`. `injectCloudAPIKeys` could derive the map from `cloudProviderRegistry` and the hardcode would vanish. **Zero Swift tests cover it** — `apps/netllm-mac/Tests/NetllmMacTests/NetllmMacTests.swift` is the only Swift test file and contains no `Keychain`/`cloudProvider`/`injectCloud` reference.

### Enforcement present vs missing

**Present (tripwires):** three exhaustive `==` set assertions — `test_cloud_providers.py:25`, `test_admin_cloud.py:32`, `test_admin_cloud.py:196` — fail the moment you add to `CLOUD_PROVIDERS`. They don't verify anything, but they force the contributor to *notice*.

**Missing — a contributor can forget all of these with a green build:**
1. `PythonRuntime.swift` env injection (silent 401, above).
2. `docs/architecture/06-dependencies.md:141-152` cloud-provider table — **the DashScope commit did not update it**; verified stale on the branch.
3. `docs/architecture/05-configuration-and-control-plane.md:26` mermaid node still reads `moonshot · zai · openai · anthropic · openrouter`.
4. `docs/cloud-providers-plan.md` §1 provider-facts table — the document `cloud_providers.py:5` cites as its own authority — never gained a DashScope row.
5. `tests/test_dashboard_cloud_tab.py:46` is a hardcoded tuple, so skipping dashboard.js is only caught if you also edit the test.

**Verdict: declarative, not special-cased** — the spec dataclass genuinely absorbs a new provider (`CloudProviderSpec` needed no new field for DashScope's 4 regions and dual endpoints). The residue is client mirrors and doc prose, and exactly one of those mirrors is load-bearing.

---

## Axis B — Add a local backend provider

**No recent example exists.** `KNOWN_PROVIDERS` has held the same four entries (`omlx`, `ollama`, `lmstudio`, `vllm`) with no addition in the visible history. That absence is itself the signal: the cost is high enough that nobody has done it.

### The duplicated-fact list — 11 parallel maps on one key

| # | File:line | What it re-states |
|---|---|---|
| 1 | `packages/netllm-discovery/src/netllm_discovery/local.py:16-21` | `KNOWN_PROVIDERS` — id + display label + default ports |
| 2 | `local.py:23-25` | `DEFAULT_API_KEYS` |
| 3 | `local.py:96-101` | `_env_port_candidates` — `OMLX_PORT`/`OLLAMA_PORT`/… |
| 4 | `local.py:164-168` | `_api_key_for_provider` — `OMLX_API_KEY`/… |
| 5 | `local.py:125` | `if provider_id == "ollama"` special case (`_ollama_env_candidates`) |
| 6 | `packages/netllm-core/src/netllm_core/models.py:37-39` | `ProviderId` Literal |
| 7 | `models.py:538-543` | `BackendOverride.resolve_api_key` — **a second copy of map #4** |
| 8 | `models.py:556` | `defaults = {"omlx": "omlx-local"}` — **a second copy of map #2** |
| 9 | `packages/netllm-core/src/netllm_core/platform.py:63-65` | `default_discovery_providers()`, per-OS |
| 10 | `packages/netllm-cli/src/netllm_cli/ui.py:16-22` | `_PROVIDER_LABELS` — **a second copy of the label in map #1** |
| 11 | `ui.py:222-250` | `offline_provider_hints` — hand-written per-provider `elif` chain |

Plus client mirrors: `dashboard.js:3` (`const PROVIDERS`), `SettingsViewModel.swift:94` (`static let providers`), `config.example.toml:20-27`, `docs/platform-matrix.md:61`, `docs/architecture/06-dependencies.md:133-139`.

**Three facts are each stated twice inside Python alone** (API-key env map: `local.py:164` and `models.py:538`; default key: `local.py:23` and `models.py:556`; display label: `local.py:17` and `ui.py:17`). None derives from another.

### Enforcement

**Present:** exactly one parity test — `tests/test_contract.py:162 test_darwin_swift_default_providers_match_python()` — which greps `SettingsViewModel.swift` for `static let providers = [` and asserts equality with `default_discovery_providers()`.

**It never runs in CI.** It is `@pytest.mark.skipif(sys.platform != "darwin")` (`test_contract.py:158-161`), and the `test` job matrix in `.github/workflows/ci.yml:33` is `[ubuntu-latest, windows-latest]`. The only macOS job (`menubar-lifecycle`, `ci.yml:72`) runs `build.sh release`, `test-menubar-e2e.sh`, `test-menubar-lifecycle.sh` — **not `pytest tests/`** (verified: neither script invokes pytest against `tests/`). So the repo's single Python↔Swift parity gate is dead in CI.

**Missing entirely:** `grep -rn "KNOWN_PROVIDERS" --include=*.py .` returns **only source files, zero tests**. Nothing asserts the roster, that each entry has a port list, an env-var name, a label, or an offline hint. `tests/test_local_discovery.py` tests `omlx`/`ollama`/`vllm` behaviours individually, never the registry's completeness.

**What silently breaks if a step is forgotten:** skip #3 → `<NEW>_PORT` env var silently ignored. Skip #4 or #7 → key never resolved, backend 401s (and the two copies can drift against each other). Skip #6 → `ProviderId` Literal rejects the value, but `BackendOverride.provider` is where it bites, not discovery, so the failure surfaces far from the cause. Skip #10/#11 → provider shows as a raw id and offers no troubleshooting hint. All green builds.

### Genuinely clean sub-part

**Payload adaptation needs no work at all.** `packages/netllm-sdk-openai/src/netllm_sdk_openai/payload.py` is provider-agnostic: `_adapt_payload_for_sdk` splits on an allowlist of SDK-typed params (`_SDK_CHAT_PARAMS`, `_SDK_EMBEDDINGS_PARAMS`) and routes everything else to `extra_body`. The only provider-shaped item is `_FIELD_ALIASES = {"repeat_penalty": "repetition_penalty"}` (`payload.py:74-77`). Capability detection (`netllm_core/capabilities.py:53-62`) is token-based on the model id, not provider-based. **A new provider costs zero lines in both.**

**Verdict: Axis B is the real friction.** Highest duplication of any axis, and the
only axis with literally zero registry tests.

> **RESOLVED in Phase 3.** The maps below collapsed into
> `netllm_core/local_providers.py` (`LocalProviderSpec`), and
> `tests/conformance/kit_local.py` parameterizes over it. The inventory is kept
> as the *measurement that justified the work*, not as a description of the
> current tree.
>
> Two corrections to the count, recorded because an inflated number is its own
> defect. By this table's own numbering, **10 of 11** collapsed: item #6, the
> `ProviderId` Literal, is retained by design (a derived Literal blinds
> basedpyright) and is asserted by `kit_local` instead. This table also missed a
> map — `admin.py`'s doctor env-var hints — so the true pre-refactor count was
> **12**, of which 11 collapsed.
>
> The consolidation is complete on the **Python** side only. `dashboard.js`,
> `config.example.toml`, `AppConfig.swift` and `SettingsViewModel.swift` still
> hand-mirror the roster; those are ledgered in
> `tests/conformance/ledgers/mirrors.toml` and close in Phase 4, which needs a
> server-side local-provider registry route the Swift app can fetch — there is
> no such endpoint in `routes.json` today.

---

## Axis C — Add or evolve an API surface

**This axis is clean, and the SurfaceAdapter work is the reason.** Two data points, both real.

### Data point 1 — a dialect bridge costs 1–3 files

`/v1/responses` (the Codex bridge) is `packages/netllm-agent/src/netllm_agent/service/surfaces/responses.py`, **80 lines**, with **no `Surface.RESPONSES` enum member and no adapter** (`responses.py:3-7`). It translates at the edge and delegates to `proxy_chat_completion`. Everything below — source identity, per-source routing, scenario classification, capacity, failover — is inherited unchanged. This is the protocol working exactly as designed.

### Data point 2 — a true new `Surface` costs ~11 places, 6 of them outside the adapter

To add a genuinely new dialect (not a bridge):

| Place | File:line |
|---|---|
| adapter module | `service/surfaces/<new>.py` (new) |
| enum member | `packages/netllm-agent/src/netllm_agent/taxonomy.py:35-40` |
| dispatch map | `service/surfaces/__init__.py:37-41` |
| route registration | `packages/netllm-agent/src/netllm_agent/app.py` (inside the 548-line `create_app`) |
| **branch** — error-handler path keying | `packages/netllm-agent/src/netllm_agent/errors.py:76-107` (`_is_messages_path`) |
| **branch** — dialect selection | `packages/netllm-agent/src/netllm_agent/request_plan.py:43-51` (`api_format_for`) |
| **branch** — candidate filtering | `packages/netllm-agent/src/netllm_agent/candidates.py:41-58` (`excluded_api_formats`) |
| **branch** — exhaustion message | `taxonomy.py:96` (`if context.surface is Surface.MESSAGES`) |
| **mirror** — cross-package Literal | `packages/netllm-core/src/netllm_core/models.py:41-44` `SurfaceName = Literal["chat","embeddings","messages"]` |
| **mirror** — the gate's own list | `scripts/check-engine-erosion.py:38` `SURFACE_MEMBERS = ("CHAT","EMBEDDINGS","MESSAGES","RESPONSES")` |
| contract vectors + route contract | `tests/contract/` (146 JSON vectors), `tests/test_contract.py:16` `EXPECTED_HTTP_ROUTES` |

### Enforcement — the strongest on any axis

`scripts/check-engine-erosion.py` is a genuine AST gate, wired into `scripts/ci.sh:31` (`run_lint`), which is the `lint` job that every other CI job `needs:`. It fails the build if `service/engine.py` imports any `surfaces/` module except `base`, names `Surface` or a member, reads `.surface`, or `isinstance`-tests a concrete adapter. It also handles the Phase-9 rename (`_normalize` at `:47-60` strips both `netllm_agent.` and `service.` prefixes). Backed by a second entry point at `tests/contract/test_engine_erosion.py`.

### What is missing

1. **The gate polices only `engine.py`.** The four `Surface` branches above (`candidates.py:58`, `request_plan.py:51`, `taxonomy.py:96`, `errors.py:76`) live outside its jurisdiction and can multiply freely. `base.py` documents this at `:395-405` (the `fallback_tiers` seam pulled one such branch out of `build_candidates`) — the pattern is understood, just not enforced beyond the loop.
2. **`SURFACE_MEMBERS` at `check-engine-erosion.py:38` already contains `"RESPONSES"`, which is not a member of the `Surface` enum** (`taxonomy.py:38-40` has only CHAT/EMBEDDINGS/MESSAGES). Harmless today — it proves the literal list drifts because nothing derives it. The file comments the choice as deliberate (`:37`: "importing the enum here would make the gate depend on the code it polices"), which is defensible, but it means adding a Surface and forgetting this line silently narrows the gate.
3. **`/v1/responses` is absent from `EXPECTED_HTTP_ROUTES`** (`tests/test_contract.py:16-23` lists `/v1/models`, `/v1/chat/completions`, `/v1/embeddings`, `/v1/messages` — no `/v1/responses`). The newest surface is not in the route contract. The set is additive-only, so a dropped route on an unlisted path is undetected.
4. **`app.py` is not modularized.** `service.py` → `service/` (16 modules) and CLI `main.py` → `commands/` (10 modules) both happened; the route layer did not. All 24 routes are closures inside one `create_app` function, 548 lines. This is the one place axis C's cleanliness stops.

**Verdict: already clean, as suspected.** The protocol genuinely absorbs a new surface; the residue is four escaped branches, two literal mirrors, and an unmodularized route layer.

---

## Axis D — Add a CLI command / control-plane setting

**Real example:** `bf67238 feat(sources): harness detection, one-click toggle, and logos` — a CLI command family + config field + all three control surfaces.

**32 files, +1268/−13.** Breakdown:

- Backend (5): `known_harnesses.py` (new, 87), `harness_detection.py` (new, 39), `models.py` (+4), `admin.py` (+36), `app.py` (+10)
- CLI (2): `main.py` (+74 — pre-split; today this is `commands/sources.py` + one `add_typer` line in `main.py:74`), `config_merge.py` (+1)
- Web dashboard (4): `dashboard.js` (+96), `dashboard.css` (+27), plus `static/icons/harnesses/*.svg` (6 files)
- macOS (3): `SettingsViewModel.swift` (+37), `SettingsWindowView.swift` (+82), `AgentAPI.swift` (+61)
- Tests (6): 121 + 126 + 18 + 1 + 62 + 32 lines
- Docs/skills (5): `AGENTS.md`, `docs/cli-source-routing-plan.md` (+257), and **`SKILL.md` duplicated verbatim across `.agents/`, `.claude/`, `.cursor/`, `.github/`**

### Duplicated facts

1. **The skill file exists in 4 copies** (`.agents/skills/netllm-connect-editor/SKILL.md` + `.claude/` + `.cursor/` + `.github/`), all edited identically in this commit. Mitigated by `scripts/sync-agent-skills.sh` — but that script is **not in `scripts/ci.sh`**, so a hand-edit of one copy diverges silently. `CLAUDE.md` documents the convention; nothing enforces it.
2. **Three control surfaces implement the same feature independently.** `dashboard.js:1975-2083 renderSourcesTab` (96 new lines of hand-written JS), `SettingsWindowView.swift` (+82 lines of SwiftUI), CLI `commands/sources.py`. No shared descriptor.
3. **Dashboard tab registration is 3 places**: `static/index.html:37` (`<button data-tab="sources">`), `index.html` (`<section id="tab-sources">`), `dashboard.js:2499` (`sources: renderSourcesTab` in the renderer map).
4. **CLI registration is 2 places**: `commands/<name>.py` module, `main.py:57-78` (one `app.command()`/`add_typer` line each). This part is genuinely clean — `main.py` is 82 lines total and is a pure registration table.

### Enforcement

**Present:** per-surface tests exist and are decent — `tests/test_cli_sources.py` (126), `tests/test_admin_harnesses.py` (121), `tests/test_known_harnesses.py`, `tests/test_harness_detection.py`, plus `tests/test_cli_patch_targets.py` which locks the `commands/` split's patch targets (documented at `commands/__init__.py:4-6`). `tests/test_dashboard_cloud_tab.py` / `test_dashboard_config_schema.py` grep `dashboard.js` for renderer names, so a deleted JS renderer is caught.

**Missing:**
- **No test asserts three-surface parity.** Nothing fails if a setting lands in the CLI and the config schema but never reaches `dashboard.js` or SwiftUI. The `renderSourcesTab`-in-body greps only catch *deletion* of something already there.
- Swift builds only on `macos-14` in the `menubar-lifecycle` job; a SwiftUI compile error is caught, a *missing* control is not.
- `scripts/sync-agent-skills.sh` is not a CI check.

**Verdict: high file count, but ~1100 of the 1268 lines are genuine per-surface work, not duplication.** The tax is that "keep three control surfaces in step" is a convention with no gate. `dashboard.js` at 2826 lines and `SettingsWindowView.swift` at 1237 lines are the two files that will keep absorbing this — both are the pre-split shape that `service.py` and `main.py` already escaped.

---

## Axis E — Config schema and wire-contract evolution

### Genuinely clean: the schema derivation

`packages/netllm-core/src/netllm_core/config_schema.py` (169 lines) walks the pydantic models and emits a UI-form document, served at `GET /netllm/v1/config/schema`. Two real derivation gates in `tests/test_config_schema.py`:
- `:24 test_sections_match_netllm_config_fields`
- `:28 test_every_pydantic_field_has_a_schema_entry`

These are the best enforcement in the repo: **you cannot add a pydantic field without it appearing in the schema document.** Downstream, `discovery`, `swarm`, and `ui` are fully schema-driven — `NetllmConfigDocument.swift:15-25` stores them as raw `[String: JSONValue]`, so a new field in those three sections costs **1 file** (`models.py`) and renders automatically on both clients.

### The friction

**1. Half the sections are still hand-mirrored typed Swift structs.** `agent` (`NetllmConfigDocument.swift:28-35`), `routing` (`:49-88`), `cloud` (`:99-119`) are typed `Codable` structs. Adding a scalar to any of them: the field round-trips (the merge preserves omitted keys — `config_merge.py:47-58 deep_merge`, case 3) but is **invisible and uneditable in the macOS app** until someone hand-adds it. Additionally `UiSection` (`:122-150`) re-mirrors the `ui` keys by hand *despite* `document.ui` being dynamic — a mirror of a section that was explicitly de-mirrored.

**2. Two hand-maintained field allowlists that shadow pydantic models, with zero enforcement:**
- `config_merge.py:155-168` — `_merge_sources` iterates a literal tuple of 13 `SourceConfig` field names.
- `config_merge.py:193-201` — `_merge_cloud_providers` iterates a literal tuple of 7 `CloudProviderConfig` field names.

I verified them against the models programmatically — **both are in sync today, and nothing keeps them that way**:
```
SourceConfig fields not in merge allowlist:        [] (excluding id, secret)
CloudProviderConfig fields not in merge allowlist: [] (excluding api_key)
```
Add a field to `SourceConfig` and it will pass `test_every_pydantic_field_has_a_schema_entry`, render in the dashboard and macOS Settings, and be **silently discarded on save** from every control surface. This is a two-line test away from being closed (`set(SourceConfig.model_fields) - allowlist == {"id","secret"}`) and it does not exist.

**3. A third registry with the same problem:** `config_merge.py:40-44` `_FULL_REPLACE_DICT_PATHS` lists `(routing, model_pools)`, `(routing, model_aliases)`, `(discovery, provider_urls)`. A new dict-valued config field not registered here gets case-3 deep-merge instead of full-replace — **entry deletion silently does not persist.** That exact bug is what commit `0c4489d fix(config): unify CLI/dashboard merge — dict entry deletion now persists` was filed to fix. It is a known failure mode with a hand-maintained opt-in list and no gate.

**4. `_CONFIG_SECTIONS` at `config_merge.py:37`** duplicates `SECTIONS` at `config_schema.py:34-41` and `NetllmConfig`'s own field names. Three statements of the section roster; only the schema↔`NetllmConfig` pair is tested (`test_config_schema.py:24`).

### Structurally missing: there is no versioning at all

`grep -rn "schema_version|config_version|migrat|deprecat" packages/netllm-core/src/` returns **three hits, all incidental**:
- `cloud_providers.py:8` — "not a config migration"
- `known_harnesses.py:9` — "not a config migration"
- `models.py:451` — a one-shot boolean flag (`ensure_cloud_defaults`)

There is **no config schema version field, no migration framework, no deprecation mechanism, no upgrade path**. Compatibility is handled ad hoc and by convention:
- kept-for-compat no-ops, e.g. `routing.require_same_model_for_shard` (documented in `packages/netllm-core/AGENTS.md`)
- `tests/test_contract.py:65 test_legacy_routing_strategies_still_accepted`, `:127 test_provider_ids_accept_legacy_values`, `:269 test_heartbeat_accepts_legacy_v03_backend_rows`

These are individually good tests, but they are a **hand-written list of remembered legacies**, not a mechanism. There is no way to mark a field deprecated, no way for a client to know which schema generation it is talking to (`config_schema.py` embeds `get_version()`, i.e. the app version, not a schema version), and no migration hook when a field is renamed or removed.

**5. `config.example.toml` is unenforced documentation.** The only test touching it is `tests/test_contract.py:117 test_config_example_roundtrip`, which asserts it *parses* and that two fields survive a round-trip. Nothing checks that every config field is documented there, so a new field is undocumented by default with a green build.

**Verdict: the schema-derivation half is excellent; the evolution half does not exist.** This is the axis where the stated goal ("standard integration and update pathways, versioned, with migration") has the largest gap between intent and code.

---

## Ranked recommendations, by ratio of risk closed to effort

1. **`PythonRuntime.injectCloudAPIKeys` should derive from `cloudProviderRegistry`.** `api_key_env` is already served (`admin.py:232`). Deletes the single silent, load-bearing hardcode on axis A. (~10 lines Swift.)
2. **Two allowlist-parity tests** asserting `_merge_sources` / `_merge_cloud_providers` field tuples equal `SourceConfig.model_fields` / `CloudProviderConfig.model_fields`. Closes the "new config field silently unsavable" hole. (~15 lines Python.)
3. **Collapse the 11 local-provider maps into one `LocalProviderSpec` dataclass** (mirroring `CloudProviderSpec`, which demonstrably works) and add the roster test that `KNOWN_PROVIDERS` currently lacks entirely. This is axis B's whole cost.
4. **Run `pytest tests/` on the `macos-14` CI job** (or drop the `skipif` and grep the Swift file on any platform). The repo's only Python↔Swift parity gate currently never executes.
5. **Introduce a config `schema_version` + a migration registry**, and derive `_FULL_REPLACE_DICT_PATHS` / `_CONFIG_SECTIONS` from model introspection. Axis E's structural gap.
6. **Extend `check-engine-erosion.py` to the four escaped `Surface` branches** (`candidates.py:58`, `request_plan.py:51`, `taxonomy.py:96`, `errors.py:76`) — or accept them as declared seams the way `check-service-split-mechanical.py` declares its own.
7. **Modularize `app.py`'s route layer** and add `scripts/sync-agent-skills.sh --check` to `ci.sh lint`.

**Cleanest axis: C** (SurfaceAdapter — the anti-erosion gate is real and the Responses bridge proves the seam absorbs a new surface for ~80 lines). **Real friction: B** (11 duplicated maps, zero tests), then **E** (no versioning mechanism at all).

**Files of interest, absolute paths:**
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/cloud_providers.py`
- `/home/user/llm-swarm-router/packages/netllm-discovery/src/netllm_discovery/local.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/models.py`
- `/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/config_merge.py`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/service/surfaces/base.py`
- `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/app.py`
- `/home/user/llm-swarm-router/scripts/check-engine-erosion.py`
- `/home/user/llm-swarm-router/apps/netllm-mac/Sources/Server/PythonRuntime.swift`
- `/home/user/llm-swarm-router/.github/workflows/ci.yml`
- `/home/user/llm-swarm-router/tests/test_contract.py`