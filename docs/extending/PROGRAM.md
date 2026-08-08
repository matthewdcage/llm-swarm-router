## THE ADOPTED PROGRAM — Registry-First, gated by Conformance Kits

**Winner:** `registry-first` — it is the only program that *deletes* the duplicated facts, and every mechanism it uses already exists in this tree.

**Grafted in:**
- from `contract-kit`: registry-parameterized conformance kits, the expiring ledger (`reason` + `expires`, empty by default), platform-independent text projections (fixes the dead darwin-only parity gate), and the "zero vectors changed" property used as the reviewability budget per phase.
- from `plugin-boundary`: its Phase 0 (seven cheap independent fixes), the `CloudConfig` unknown-key drop finding (`models.py:470-475`), the four-version-axes separation, and the compatibility-promise text.

**Rejected outright:** the plugin boundary itself. No `netllm_ext` package, no `importlib.metadata` entry points, no `[extensions]` config section, no out-of-tree example distribution, no opening `CloudProviderId`/`ProviderId`/`SurfaceName` from `Literal` to `str`. Reasons in §7.

Baseline for every exit criterion: `main @ 243e3dc`, 1113 passed / 4 skipped, `scripts/ci.sh lint` exit 0.

---

## 1. The single rule

> A fact is stated **once**, in a frozen spec dataclass in a registry. Everything downstream is either **derived at runtime** (server projects the registry; clients fetch it), **generated at build time** with `--check` in `run_lint` (things that must exist before an agent runs), or **projection-tested** (hand-written mirrors that must match the registry, asserted as text on any OS). Anything in none of those three categories is a mirror, and `check-registry-mirrors.py` fails the build on it.

Precedents already in-tree, none of which this program replaces:
- `CloudProviderSpec` / `CLOUD_PROVIDERS` (`cloud_providers.py:29-48`) — the spec-registry pattern, proven by `08946b6` absorbing DashScope with **no new field**.
- `admin.cloud_provider_registry_payload()` (`admin.py:215-235`) — the runtime projection, already preferred by both clients.
- `scripts/generate-dashboard-tokens.py --check`, wired at `ci.sh:29` — the generation rail.
- `scripts/check-engine-erosion.py` (`ci.sh:31`) + `check-service-split-mechanical.py:36-44` `DECLARED_SEAMS` — the AST gate and the declared-exception list.
- `tests/contract/canonical.py:1-27` (declare-or-fail), `allowed-divergences.txt` + `test_divergence_lint.py` (change-requires-a-named-reason), `test_surface_adapters.py:35-52` (roster parameterization).

---

## 2. Phase 0 — stop NEW duplication first (1.5 days, pure add)

The audit's recurring failure is "added to one surface, forgotten in the others." Phase 0 buys the **detector** before any refactor, so every later phase is provably behaviour-preserving and no new mirror can land in the meantime.

**0a. `scripts/check-registry-mirrors.py` — ship it in Phase 0, not Phase 3.** This is the one amendment to the winner that matters most. Scope it deliberately narrow so it is writable in half a day and produces no false positives:

- Input: `tests/conformance/ledgers/mirrors.toml`. Each entry: `fact_class` (e.g. `cloud-provider-id`), `source` (the registry module), `allowed_mirrors` (explicit file globs, each with `reason` and `expires`).
- Rule: for each fact class, scan a **fixed file list** (not the whole repo) for the literal ids drawn from the live registry. Any hit outside `source` ∪ `allowed_mirrors` fails.
- Seed the ledger with *every mirror that exists today*, each with `expires` set to the phase that will eliminate it. So it is green on day one and its only power on day one is: **a new provider/surface id cannot appear in a new file.** That is exactly the class to kill first, and it costs ~120 lines.
- No AST, no markdown prose scanning in Phase 0. Files scanned: `dashboard.js`, `SettingsViewModel.swift`, `KeychainStore.swift`, `PythonRuntime.swift`, `config.example.toml`, `ui.py`, `platform.py`, `local.py`, `models.py`. Doc rosters join in Phase 3 when they become generated.

**0b. The cheap independent fixes** (from `plugin-boundary` P0, all zero-architecture):
1. `PythonRuntime.injectCloudAPIKeys` (`PythonRuntime.swift:79-85`) derives `api_key_env` from `cloudProviderRegistry` — already on the wire at `admin.py:232`. Deletes the repo's only silent, load-bearing hardcode. Land the first Swift unit test for it (`NetllmMacTests.swift` currently references no Keychain/cloudProvider symbol).
2. Two allowlist-parity tests: `_merge_sources` tuple == `set(SourceConfig.model_fields) - {"id","secret"}`; `_merge_cloud_providers` == `set(CloudProviderConfig.model_fields) - {"api_key"}`. ~15 lines; closes "new config field silently unsavable on every control surface."
3. `KNOWN_PROVIDERS` roster test — the axis currently has **zero** tests referencing it.
4. `EXPECTED_HTTP_ROUTES` → generated `tests/contract/routes.json`, **exact-set** equality (today: 17 of ~28 routes, presence-only, so deleting `/v1/responses` is CI-green).
5. Drop the `skipif` at `test_contract.py:158-161` — it greps a checked-in `.swift` file and needs no macOS. Also add `uv run pytest tests/ -m "not slow"` to the `macos-14` job. Do both; they fail differently.
6. Fix `status.py:84-89`: use `update.compare_versions` (`update.py:47-61`) instead of string inequality, and make the message name the actually-older peer.
7. Fix `docs/sdk-versions.md:41` (routes to `service.py`, dissolved by F-24) and add `scripts/check-doc-paths.py` (~30 lines) to `run_lint`.
8. `scripts/sync-agent-skills.sh --check` into `run_lint`.
9. `DEPRECATIONS` registry + expiry test, seeded with the two already-expired shims (`models.py:296-300` added in 0.4.6, tree is 0.5.0.1; `pool.py:394-396`).

**Exit:** ~380 LOC added, 0 production behaviour changed except items 1 and 6; 1113+ tests pass; **0 contract-vector diffs**; `mirrors.toml` green and now blocking new mirrors.

---

## 3. Per-axis target and its executable conformance contract

Every axis ends with a **kit**: `tests/conformance/kit_<axis>.py`, parameterized over the registry, so a new entry acquires its full suite with zero test-file edits. Shared helpers in `tests/conformance/projections.py` (text-parse Swift/JS/TOML/MD, return `(values, source_location)` so failures read `SettingsViewModel.swift:94 is missing 'dashscope'`).

### Axis A — cloud provider
- Spec unchanged except `keychain_account: str = ""` (defaults `f"{id}_api_key"`, matching `KeychainStore.swift:30`'s existing generic arm) and optional `models_hook`.
- `KeychainStore` switch cases (`:24-29`) deleted — the default arm is already correct.
- `dashboard.js` bootstrap, `SettingsViewModel.swift` fallback, `config.example.toml` stanza, and the 5 doc rosters become **generated** between markers.
- **Contract:** ~9 parameterized tests/provider — spec well-formed; `default_region() in endpoints`; `models_endpoint is False ⟹ static_models` non-empty (the Z.ai invariant); payload round-trip; `CloudProviderConfig` load→save→load survives (**this is the `models.py:470-475` drop bug, as a test**); every projection matches; regex asserting `PythonRuntime.swift` contains no literal `_API_KEY` table; one golden vector per provider.
- **Proof obligation:** replay `08946b6` (DashScope) against the finished axis. Target ≤3 files. Record the measured number in the guide.

### Axis B — local backend provider (largest win)
New `packages/netllm-core/src/netllm_core/local_providers.py` (**core**, not discovery — `models.py`, `ui.py`, `platform.py` all need it and discovery already depends on core). `LocalProviderSpec`: `id`, `display_name`, `default_ports`, `port_env`, `api_key_env`, `default_api_key`, `platforms`, `offline_hint`, `host_env` (contract-kit's cleaner encoding of the Ollama case: `host_env="OLLAMA_HOST"` makes `local.py:125`'s `if provider_id == "ollama"` disappear into a generic `_host_env_candidates(spec)`), plus optional `extra_url_hook` / `probe` callables for anything else.

All 11 maps collapse. Note the asymmetry contract-kit found: `ui.py:16-22` has **five** keys — the four discovery providers plus `"custom"` — so the label projection parameterizes over `LOCAL_PROVIDERS ∪ {"custom"}` while port/probe parameterizes over `LOCAL_PROVIDERS` alone.

`ProviderId` stays a hand-written `Literal` (basedpyright is configured; a derived Literal blinds it) with a `get_args` equality assertion.

- **Contract:** ~9/provider — candidate URLs non-empty for every default port on both `127.0.0.1` and `localhost`; `<PORT_ENV>=9999` monkeypatched appears in candidates; `<API_KEY_ENV>=sentinel` resolves through **both** `_api_key_for_provider` and `BackendOverride.resolve_api_key` (kills the two-copy drift); `default_discovery_providers()` membership iff `sys.platform in spec.platforms`; label and offline hint reach `ui`; probe against a FakeFarm at the default port; all five client/doc projections.
- **Untouched, correctly:** `netllm_sdk_openai/payload.py` (allowlist + `extra_body`, provider-agnostic) and `capabilities.py:53-62` (model-id-token-based). A new local provider costs zero lines in both.

### Axis C — API surface
- Four escaped branches (`errors.py:76-107`, `request_plan.py:43-51`, `candidates.py:41-58`, `taxonomy.py:96`) become `SurfaceAdapter` members with **identical values** — the move `base.py:395-405` already documents having been done once for `fallback_tiers`. `errors.py` stamps `request.state.surface` at route registration and falls back to path-keying only for unstamped requests, which is what `base.py:38-49` already anticipates.
- `PROTOCOL_MEMBERS` (`test_surface_adapters.py:35-52`) becomes derived from `SurfaceAdapter.__protocol_attrs__` — it is itself an instance of the duplication its file exists to prevent.
- `SURFACE_MEMBERS` (`check-engine-erosion.py:38`) — **keep the literal**; its `:37` comment defending import-freedom is correct. Add a test-side assertion `set(SURFACE_MEMBERS) >= {m.name for m in Surface}`. (It contains `RESPONSES`, not a member — proof it drifts.)
- `drivers.py:26-45`'s three parallel literals (`PATHS`/`_ROUTE`/`_STREAMING`) collapse to one `PathSpec`.
- **`check-engine-erosion.py --seams`**: extend from one file to a repo scan counting `Surface`-keyed branches outside `surfaces/`, compared to `ledgers/surface-seams.toml`, seeded with the four known branches, each with the adapter member that would absorb it.
- **`app.py` route modularization is scheduled** (contract-kit declined it; it is the one real structural gap on the cleanest axis) but **split from the branch move** — see Phase 5a/5b.
- **Contract:** every `Surface` member resolves through `adapter_for`; no `NotImplementedError` stub survives; `wire_error` envelopes distinct per dialect; `routes.json` exact match; **every route with `surface != null` has ≥1 vector** — this is what makes "does the protocol absorb a new surface" CI-answerable.

### Axis D — CLI / control plane
`ControlDescriptor` in `netllm_core/control_plane.py`: `key`, `kind`, `cli`, `dashboard_renderer`, `swift_symbol`, `admin_route`, `surfaces_required`, `intentionally_absent: dict[surface, reason]`. It describes **presence obligations, not widgets**. Derived from `config_schema.py`'s existing field walk where possible; served additively on the existing `GET /netllm/v1/config/schema` (no new endpoint, no client break).

- **Contract:** CLI name resolves via Typer introspection (real, not grep); `dashboard_renderer` present in `dashboard.js` **and** the renderer map at `:2499` **and** has matching `index.html` button + section; `swift_symbol` in `SettingsWindowView.swift`; `admin_route` in `routes.json`; any required-but-absent surface must carry an `intentionally_absent` reason **with an expiry**. This is F-21 (`07-findings-register.md:625-641`), never implemented, and the repo's first cross-surface parity gate.
- `main.py` is untouched — 82 lines, already a pure registration table.

### Axis E — config and wire evolution
1. **Unknown-key preservation — the highest-severity item in the whole program.** `NetllmConfig` (`models.py:481-487`) declares no `model_config`, so `extra="ignore"` applies; `load_config` (`:657-664`) drops unknowns and `save_config` (`:677-690`) rewrites the entire file from `model_dump()`. Every write path — `POST /netllm/v1/admin/config` (`app.py:387`), `netllm config import` (the macOS **Save** button), `netllm join` — silently deletes newer keys. Fix: `ConfigDict(extra="allow")` on `NetllmConfig` and each section model, `_preserved_extras` carried through `config_merge.apply_config_patch`, re-emitted by `save_config` with a one-line warning naming them. Also fix `CloudConfig`'s filtering validator (`models.py:470-475`) which drops unknown provider subtrees — preserve and report in doctor instead.
2. **`schema_version: int`** at config top level, starting at **2** (absent ⇒ 1). Distinct from `config_schema.py:164`'s `get_version()`, which is an app-version ETag — keep both, never conflate.
3. **`config_migrations.py`** — ordered `Migration(from_version, to_version, apply: dict->dict, notes)`, run in `load_config` between `tomllib.loads` and `model_validate` (a two-line insertion at the single chokepoint). Pure dict→dict, unit-testable with no filesystem. Golden before/after pairs in `tests/fixtures/config-generations/`. `save_config` writes `config.toml.bak-v{n}` before the first migrated write. `netllm config migrate --dry-run` in `commands/config_io.py`.
4. **`docs/deprecations.toml`** with `deprecated_in` / `remove_in` / `replacement`; kit fails when `compare_versions(get_version(), remove_in) >= 0` and the symbol still exists; `load_config` emits a `DeprecationWarning`; `netllm doctor` lists deprecated keys in the user's actual config. This is the clock G4 says does not exist.
5. **Derive** `_CONFIG_SECTIONS`, `_merge_sources`, `_merge_cloud_providers`. **Keep** `_FULL_REPLACE_DICT_PATHS` hand-declared (full-replace vs deep-merge is genuine semantics, not derivable) but add a completeness test: every `dict[str, X]`-typed field on any section model must be in it or in an explicit `_DEEP_MERGE_DICT_PATHS` twin. That is the gate for the bug class `0c4489d` was filed to fix.
6. **Delete `BOOTSTRAP_SECTIONS`** (`config_schema.py:44-46`) and its test. No client imports it; `dashboard.js:276-287` hand-rolls working fallbacks; `ConfigStore.swift:40-44` reaches the schema through the *bundled* CLI so it can never be skewed. A tested constant used by nothing is worse than no constant.
7. `config.example.toml` completeness test: every schema field documented or ledgered. Today only `test_contract.py:117` asserts it parses.

---

## 4. Versioning, compatibility, and the mixed-version mesh

**Four axes, never conflated** (`docs/versioning.md`):

| Axis | Where | Scheme |
|---|---|---|
| App version | `pyproject.toml`, `test_version_sync.py:38-63` | 4-part `X.Y.Z.N` (currently *stated nowhere* — state it) |
| Config schema | `schema_version` in config.toml | monotonic int |
| Wire generation | `/netllm/v1/status` + heartbeat + mDNS TXT | monotonic int |
| Telemetry schema | already exists (`telemetry.py:272`) | monotonic int — the template the others copy |

**The promise** (`docs/compatibility-policy.md`), promoting `platform-matrix.md:24`'s one line:
- `/v1/*` dialect routes: additive only, no sunset. Enforced by `routes.json` exact-set equality.
- `/netllm/v1/*`: additive within a wire generation; removal requires a generation bump plus one minor of overlap, old shape served with `"deprecated": true`.
- Config: forward-compatible **both** directions. Old agent + new config ⇒ unknown keys preserved. New agent + old config ⇒ migrations run.
- Mesh skew: **N−1 minor fully supported, N−2 degraded, beyond that doctor error.**

**Mesh upgrade story** (`docs/mesh-upgrade.md` — no such doc exists in any form today; `commands/join_swarm.py:140` tells operators to verify "a compatible netllm version" without ever defining it):
- **Ordering: gateway first.** `swarm_tasks.py:28-52` makes the gateway authoritative for routing strategy, so a newer gateway can serve older peers, not the reverse. (Note: `plugin-boundary` argues gateway *last* from the same evidence — adjudicated in favour of gateway-first because the authoritative node must understand the superset, and a strategy an older peer does not know degrades to that peer's default rather than corrupting it. Write the reasoning into the doc so it can be challenged with data.)
- **The preservation fix (§3E.1) is what makes rolling upgrade safe at all** — it turns the five-machine scenario (upgrade one box, configure a provider, hit Save on an older box) from lossy to lossless. ~30 lines.
- **Version comparison unified:** `tests/contract/version-ordering.json`, consumed by both `update.compare_versions` and a new Swift test. Two algorithms with no shared corpus today; Python's mis-orders prereleases (`1.0.0-rc.1` → `[1,0,0,1]`), masked only because `fetch_latest_release` filters them — so an operator *running* a prerelease is the exposed case.
- **Interop vectors:** `tests/contract/interop/` driving old-shape payloads at each `/netllm/v1/*` route, plus a mixed-version lane extending `tests/test_e2e_two_agents.py` with `NETLLM_COMPAT_PRETEND_VERSION` one minor back, asserting combined catalog works, `peer_warnings` fires once naming the right machine, and a save on the older node preserves the newer node's keys. Marked `slow`, excluded from the macOS job. Today the only mixed-version test is `test_contract.py:269-297` — heartbeat backend rows, v0.3.x shape.
- Extend `test_version_sync.py` to `Formula/netllm.rb:6` and `packaging/windows/winget/netllm.yaml:3` (**stale at 0.2.3.2**), and add a header to the winget file stating it is a template regenerated by `update-winget-manifest.ps1` at release — it currently reads as a live manifest.

---

## 5. Phases — each independently mergeable and reversible

Gate on every phase: `uv run pytest tests/contract/test_vectors.py` with **zero diff** to any of the 146 JSON vectors, and `allowed-divergences.txt` unchanged. Phases that add vectors add *new files only*.

| # | Phase | Entry | Exit criterion | Effort |
|---|---|---|---|---|
| **0** | Mirror gate + cheap fixes (§2) | none | `mirrors.toml` blocking; PythonRuntime derives; 2 allowlist tests; routes.json exact; darwin skipif gone; deprecation clock seeded; 0 vector diff | **1.5 d** |
| **1** | Kit skeleton: `tests/conformance/` + `registry.py` + `projections.py` + empty ledgers | 0 | runs and passes with zero registries wired; no production file touched | **1 d** |
| **2** | **Axis E part 1 — config write safety** | 0 | `[future_section]` + `agent.future_field` survive load→save, proven by test; `CloudConfig` stops dropping providers; merge allowlists derived; section roster three-way equality; dict-field classification test; `BOOTSTRAP_SECTIONS` deleted. **Not mechanical** — needs a release note | **2 d** |
| **3** | **Axis B — `LocalProviderSpec`** | 1 | 11 maps → 1; `grep -rn 'OMLX_PORT\|OLLAMA_API_KEY\|omlx-local' --include=*.py packages/` returns one file; `kit_local` green ×4; `test_local_discovery.py` unchanged and passing; 0 vector diff | **3 d** |
| **4** | **Axis A close-out + generation rail** | 3 | `generate-registry-artifacts.py --check` in `run_lint`; JS/Swift bootstrap + `config.example.toml` + 5 doc rosters generated; `kit_cloud` green ×5; **DashScope replays in ≤3 files**; 0 vector diff | **3 d** |
| **5a** | **Axis C — branch absorption** (pure motion) | 1 | zero `Surface` references at the four ex-branch sites; `PROTOCOL_MEMBERS` derived; `drivers.py` literals collapsed; `--seams` + ledger live; **0 vector diff** | **3 d** |
| **5b** | **Axis C — `app.py` → `routes/`** | 5a, 4 | `create_app` ≤150 lines; `routes.json` unchanged set, new registration sites; 0 vector diff | **3 d** |
| **6** | **Axis E part 2 — versioning, migration, mesh** | 2 | `schema_version` emitted; one no-op migration + golden pair; `config migrate --dry-run` ships; expired deprecation fails CI (demonstrated); mixed-version lane green; version-ordering corpus consumed by Python **and** Swift | **4 d** |
| **7** | **Axis D — `ControlDescriptor` + parity** | 4, 6 | every field rendered on all three surfaces or ledgered with an expiry; parity test **demonstrated red** by a deliberate temporary deletion | **4 d** |
| **8** | Docs, DOX, worked-example tests | 1-7 | `docs/extending/` complete; 6 `AGENTS.md` extension contracts; `tests/extending/test_worked_example_<axis>.py` green; every doc path reference resolves | **3 d** |

**Total ≈ 27.5 days.** Phases 0 + 2 alone (3.5 days) close the two highest-severity defects in both input maps. **Coherent stopping points: after 0, after 3, after 6.** Stopping mid-phase is never required because no axis is ever half-migrated — Phase 3 moves all four local providers or none.

**Reversibility:** every phase is a single revertible commit range; the vector corpus is the behaviour gate, so a revert is provably a no-op on the request path. Phase 2 is the only one that changes user-visible write semantics and therefore is the only one requiring a `docs/release-notes/` entry.

**Sequencing changes from the winner's plan, and why:**
- `check-registry-mirrors.py` pulled from Phase 3 to Phase 0 (narrower scope, ledger-seeded). Without it, the audit's core failure class stays open through the highest-churn phases.
- Config write safety (was Phase 4) promoted to Phase 2. It is the only actively destructive bug in the inventory and it is independent of every registry.
- Phase 5 split into 5a (branch motion, vector-critical) and 5b (route modularization, mechanical but large). The winner's single 5-day unit bundled the riskiest change in the program with a large file move; reviewing them separately is the whole point of the "zero vectors" property.

---

## 6. What is NOT worth doing

1. **The plugin boundary.** No `netllm_ext`, no entry points, no `[extensions]` section, no out-of-tree example package. It would design a 1.0 public API from N≈0 third-party consumers, cost 6-11 extra days, open an in-process unsandboxed code path in a process holding cloud API keys and a LAN port, and buy nothing the in-tree registry does not already buy. If a genuine third party ever appears, the registries are already the right shape to expose — the decision is deferrable at near-zero cost, which is the argument for deferring it.
2. **Opening `ProviderId` / `CloudProviderId` / `SurfaceName` to validated `str`.** Real typing regression (pydantic parse-time rejection, editor completion, basedpyright exhaustiveness) for no benefit while all entries are in-tree. Keep the `Literal`s, assert them with `get_args` equality.
3. **Generating SwiftUI or dashboard JS from a descriptor.** ~1100 of `bf67238`'s 1268 lines were genuine per-surface UI work. A three-UI generator is a framework project with a bad payoff curve. Generate the *manifest of what must exist*; hand-write the UI.
4. **Deriving `SURFACE_MEMBERS` in `check-engine-erosion.py`.** Its `:37` no-import-coupling argument is correct. Assert the superset test-side instead.
5. **Deriving `_FULL_REPLACE_DICT_PATHS`.** Full-replace vs deep-merge is a semantic choice. Gate completeness; do not synthesize the answer.
6. **Rewriting `dashboard.js` (2826 lines) or `SettingsWindowView.swift` (1237 lines).** Both are pre-split shapes that will keep absorbing axis-D work. Acknowledged debt, not scheduled — the parity gate makes their *gaps* loud without touching their *size*.
7. **Live cloud-provider canaries.** Real gap (`docs/cloud-providers-plan.md` flags it: no per-provider validated-date, no canary), but structural conformance cannot catch a wrong `base_url` and this program will not pretend to. Separate follow-on modelled on `sdk-canary.yml`.
8. **Anthropic SDK param-drift protection (G5).** Real and worth doing; out of scope here because it belongs to the already-excellent SDK-bump pathway, not the extensibility axes. File it, do not bundle it.

---

## 7. What stays manual, and why

- **Per-surface UI implementation** (JS, SwiftUI, CLI output formatting). Genuine work; the descriptor makes omission a build failure, not a smaller job. Say this in `docs/extending/README.md` — overselling "adding a provider is one line" will be falsified by the first contributor and will discredit the ledgers with it.
- **Provider-specific behaviour**: probe quirks, `_FIELD_ALIASES`, Ollama's `OLLAMA_HOST`. Encoded as typed hook callables or a `host_env` field, never squeezed into data. **Hard rule:** a spec field earns its place only when ≥2 entries set it non-default; otherwise it is a hook. Review the spec shape at every third entrant.
- **Doc prose.** Only roster tables and mermaid nodes are generated (between markers). Explanatory text stays human.
- **`config.example.toml` prose and comments.** Only the field skeleton is checked for completeness.
- **Semantic correctness of any registry entry.** Structural conformance cannot tell you a base URL is wrong.

**Ledger discipline** (the escape hatch, and the thing most likely to erode): every ledger entry requires `reason` + `expires`. Stated tripwire in `docs/architecture/10-extensibility-contracts.md`: **if `local-exceptions.toml` reaches 5 entries, or `intentionally_absent` covers >20% of control descriptors, the spec is wrong — redesign it, do not add entries.**

---

## 8. Documentation and DOX placement

`docs/extending/` — `README.md` (the single rule, the four gates and what each error message means, the decision tree "data, hook, or adapter?"), `01-cloud-provider.md` … `05-config-and-wire-evolution.md`, `templates/` (copy-paste stubs). Plus `docs/compatibility-policy.md`, `docs/versioning.md`, `docs/mesh-upgrade.md`, `docs/deprecations.toml`, `docs/architecture/10-extensibility-contracts.md` (slots after the existing 01-09).

Each guide's checklist maps **1:1 to a named kit test**, and each ends with the exact invocation (`uv run pytest tests/conformance/kit_<axis>.py -k <your-id>`). "Proven" is discharged by `tests/extending/test_worked_example_<axis>.py`, which injects a fixture registry entry and asserts it flows end-to-end — discovery URLs → config validation → schema document → projection endpoint → CLI listing → dashboard payload — **with zero source edits beyond the registry entry**. If the guide's central claim stops being true, that test goes red.

**DOX rail** (root Child DOX Index + per-folder `AGENTS.md`): index gains `tests/conformance/` and `docs/extending/`. Each package `AGENTS.md` gains an **Extension contract** section naming the registry it owns, the projection it serves, and the no-new-mirrors prohibition — `netllm-core` owns `CLOUD_PROVIDERS` / `LOCAL_PROVIDERS` / `SECTIONS` / `MIGRATIONS` / `DEPRECATIONS`; `netllm-discovery` consumes only; `netllm-agent` owns `SURFACES` and `routes.json`; `netllm-cli` owns `COMMANDS`; `netllm-mac` **consumes only**, with its typed-struct mirrors (`NetllmConfigDocument.swift:28-35,49-88,99-119,122-150`) named as debt with a removal target. Root do-not rule added: *never add a provider/surface id literal outside its registry.* PR template gains one checkbox: "new fact added — is it in a registry?"

---

## 9. Risks carried forward

- **R1 — registries make the regular case easy and the irregular case ugly.** Mitigated by the hooks-not-fields rule and the ≥2-entries threshold; reviewed every third entrant.
- **R2 — generation moves duplication into the build.** Every generated file opens with a `DO NOT EDIT — regenerate with <exact command>` header (the `generate-dashboard-tokens.py:24-26` pattern); `--check` failure prints the command; generated Swift limited to degraded-mode fallback structs (~200 ms blast radius), never load-bearing logic; prefer runtime projection over codegen wherever a client can fetch.
- **R3 — projections prove presence, not correctness.** Scope them to rosters and symbol names only, never behaviour. Pair with real per-language tests where cheap (Phase 0 lands the first Swift one).
- **R4 — Phase 5a touches the D11 error path.** Corpus is the gate, exit is byte-identical vectors, no exceptions. Additionally AST-diff the moved callables against their pre-move source using `check-service-split-mechanical.py`'s technique, so the move is proved from source text independently of the tests.
- **R5 — record-mode blessing.** `NETLLM_VECTOR_RECORD=1` can bless a regression in one command and an annotation is prose, not proof. Add a CI rule: any commit touching `vectors/*.json` must also touch `allowed-divergences.txt` or a release-notes file.
- **R6 — Phase 7 is the one to cut under schedule pressure.** Worst risk-closed-per-effort ratio in the program; it buys a tripwire, not a solution. Phases 0, 2, 3, 6 carry the value.

---

### Key absolute paths
`/home/user/llm-swarm-router/packages/netllm-core/src/netllm_core/cloud_providers.py` · `.../models.py` · `.../config_merge.py` · `.../config_schema.py` · `.../platform.py` · `/home/user/llm-swarm-router/packages/netllm-discovery/src/netllm_discovery/local.py` · `/home/user/llm-swarm-router/packages/netllm-cli/src/netllm_cli/ui.py` · `/home/user/llm-swarm-router/packages/netllm-agent/src/netllm_agent/{app.py,admin.py,errors.py,candidates.py,request_plan.py,taxonomy.py,service/surfaces/base.py,service/status.py}` · `/home/user/llm-swarm-router/scripts/{ci.sh,check-engine-erosion.py,check-service-split-mechanical.py,generate-dashboard-tokens.py,sync-agent-skills.sh}` · `/home/user/llm-swarm-router/tests/contract/{canonical.py,drivers.py,test_vectors.py,test_surface_adapters.py,allowed-divergences.txt}` · `/home/user/llm-swarm-router/tests/test_contract.py` · `/home/user/llm-swarm-router/apps/netllm-mac/Sources/Server/PythonRuntime.swift` · `/home/user/llm-swarm-router/.github/workflows/ci.yml`