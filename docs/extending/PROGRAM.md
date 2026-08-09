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
6. **Rewriting `dashboard.js` or `SettingsWindowView.swift`** (2826 / 1237 lines when this program was written; 3004 / 1247 by `wc -l` on 2026-08-09 — the growth is the point). Both are pre-split shapes that will keep absorbing axis-D work. Acknowledged debt, not scheduled — the parity gate makes their *gaps* loud without touching their *size*.
7. **Live cloud-provider canaries.** Real gap (`docs/cloud-providers-plan.md` flags it: no per-provider validated-date, no canary), but structural conformance cannot catch a wrong `base_url` and this program will not pretend to. Separate follow-on modelled on `sdk-canary.yml`.
8. **Anthropic SDK param-drift protection (G5).** Real and worth doing; out of scope here because it belongs to the already-excellent SDK-bump pathway, not the extensibility axes. File it, do not bundle it.

---

## 7. What stays manual, and why

- **Per-surface UI implementation** (JS, SwiftUI, CLI output formatting). Genuine work; the descriptor makes omission a build failure, not a smaller job. Say this in `docs/extending/README.md` — overselling "adding a provider is one line" will be falsified by the first contributor and will discredit the ledgers with it.
- **Provider-specific behaviour**: probe quirks, `_FIELD_ALIASES`, Ollama's `OLLAMA_HOST`. Encoded as typed hook callables or a `host_env` field, never squeezed into data. **Hard rule:** a spec field earns its place only when ≥2 entries set it non-default; otherwise it is a hook. Review the spec shape at every third entrant.
- **Doc prose.** Only roster tables and mermaid nodes are generated (between markers). Explanatory text stays human.
- **`config.example.toml` prose and comments.** Only the field skeleton is checked for completeness.
- **Semantic correctness of any registry entry.** Structural conformance cannot tell you a base URL is wrong.

**Ledger discipline** (the escape hatch, and the thing most likely to erode): every ledger entry requires `reason` + `expires`. Stated tripwire in `docs/architecture/11-extensibility-contracts.md`: **if `local-exceptions.toml` reaches 5 entries, or `intentionally_absent` covers >20% of control descriptors, the spec is wrong — redesign it, do not add entries.**

---

## 8. Documentation and DOX placement

`docs/extending/` — `README.md` (the single rule, the four gates and what each error message means, the decision tree "data, hook, or adapter?"), `01-cloud-provider.md` … `05-config-and-wire-evolution.md`, `templates/` (copy-paste stubs). Plus `docs/compatibility-policy.md`, `docs/versioning.md`, `docs/mesh-upgrade.md`, `docs/deprecations.toml`, `docs/architecture/11-extensibility-contracts.md` (it slotted in after 10-audit-2026-08-08.md, not at 10- as planned).

Each guide's checklist maps **1:1 to a named test**, and each ends with the exact invocation. "Proven" is discharged by `tests/extending/test_worked_example_<axis>.py`, which injects a fixture registry entry and asserts it flows end-to-end — discovery URLs → config validation → schema document → projection endpoint → CLI listing → dashboard payload (→ macOS discovery checkboxes, on Axis B).

**Amended (Phase 8/8b).** This section originally promised that flow **"with zero source edits beyond the registry entry"**. Measured against the tree, that is false, and the corrected claim is:

> zero source edits beyond the registry entry **and its declared hand-written companions**, where every companion is enumerated with the reason it cannot be derived.

The companions are not defects; each is a refusal recorded above. Axis A: `CloudProviderId` (§6.2), `cloudProvidersBootstrap` (§6.3), the `[cloud.providers.<id>]` stanza in `config.example.toml`. Axis B: `ProviderId` (§6.2), `localProviderBootstrap` and `SettingsViewModel.providers` (§6.3 — two separate arrays in one file, pinned by two different tests). Two further caveats the original text did not carry: a guide checklist row may name a guard **outside** its axis kit (Axis B row 23 is in `tests/test_contract.py`), and the worked example's sufficiency property is only as strong as its stage list — a surface no stage reads cannot fail it, which is how Axis B's third companion stayed undeclared through Phase 8.

If the corrected claim stops being true, those tests go red.

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

---

# Addendum — Axes F and G (2026-08-08)

Added after the original program under-scoped two axes: its "Axis D" covered adding a
*netllm CLI command*, not integrating an **external CLI agent**; and it deferred SDK-drift
hardening that the maintainer needs in scope. Designs scored: spec-registry **8.6**
(adopted), canary-contract 7.7, capability-negotiation 6.4.

Evidence: [harness-integration-map.md](harness-integration-map.md) ·
[upstream-absorption-map.md](upstream-absorption-map.md)

## AXIS F and AXIS G — sections to append to `docs/extending/PROGRAM.md`

Base: `spec-registry`. Grafted in: `capability-negotiation`'s `FieldContract` dispositions, explicit `unknown_policy`, and the drop counter/header; `canary-contract`'s fixture recording spec (`manifest.toml`, sanitizer, `.sse` transcripts, `replay_fixture`) and its `connect --verify` attribution round-trip. Rejected: the capability-token vocabulary and request-path negotiation with a new 400 (§F.9), the live harness canary (§F.9), fixtures for all nine backends up front.

Phases 0-8 are unchanged in number, shape, entry and exit criteria. F and G attach as four field additions inside existing phases and eight new phases. Every new phase inherits the program's universal gate: `uv run pytest tests/contract/test_vectors.py` with **zero diff** to the 146 existing vectors, `allowed-divergences.txt` untouched, new vectors are new files only.

---

### 10. Axis F — CLI-agent / harness integration

**The single rule applied to a boundary fact.** A harness's *functional requirement* is stated once, in `HarnessSpec`, and every downstream statement is derived, generated, or projection-tested. Today the roster is stated 11 times in 5 languages and every functional fact is prose in two unlinked places (`connect.py:30-114`, `docs/editor-integration.md`).

**F.1 — the blocking defect, fixed in Phase 0.** `connect.py:225` validates against `KNOWN_HARNESSES`; `connect.py:241` indexes a second independent dict `_guides(...)`. A registry-only addition passes validation and hard-crashes with `KeyError` (reproduced). `grep -rn "_guides" tests/` returns nothing. One line closes it: `assert set(_guides(base_v1, base_root, key)) == {h.id for h in KNOWN_HARNESSES}`. This is the same guard `test_admin_harnesses.py:46-53` applied to the icon convention, for a failure that is louder.

**F.2 — `HarnessSpec`** (`packages/netllm-core/src/netllm_core/harnesses.py`; `known_harnesses.py` becomes a re-export shim with a `DEPRECATIONS` row and a `remove_in`). Keeps today's five cosmetic fields and adds:

```python
@dataclass(frozen=True)
class WireRequirement:
    surface: SurfaceName                    # chat|embeddings|messages|(responses, after F3)
    base_url_shape: Literal["v1", "root"]   # deletes connect.py:117-121's special case
    wire_api: Literal["chat", "responses", "messages"]
    requires_streaming: bool = False
    requires_tool_use: bool = False
    requires_fields: tuple[str, ...] = ()   # harness dialect; THE Axis-G join
    field_map: dict[str, str] = field(default_factory=dict)  # -> upstream name
    confidence: Literal["verified", "declared"] = "declared"

@dataclass(frozen=True)
class HarnessSpec:
    id: HarnessId; display_name: str; docs_url: str | None = None
    cli_commands: tuple[str, ...] = (); install_hint: str = ""
    binary_verified: bool = False; verified_at: str = ""   # replaces the prose caveat at known_harnesses.py:45-48
    detect_hook: DetectHook | None = None                  # hook, not field: only honcho needs it
    wire: WireRequirement = ...
    user_agent_needles: tuple[str, ...] = ()
    background_ua_heuristic: bool = False
    wiring: tuple[WiringStep, ...] = ()                    # env | manual (config_file deferred to F4)
    scenario_defaults: dict[Scenario, ScenarioRule] = field(default_factory=dict)
    model_rewrite_defaults: dict[str, str] = field(default_factory=dict)
    known_limitation: Limitation | None = None             # reason + expires
    verify: tuple[VerifyProbe, ...] = ()
```

`HarnessId` is a hand-written `Literal` with a `get_args` equality assertion, per §6.2. `ConfigFileWiring` is **not** a field in F2 — it has exactly one entry (Codex) and §7's ≥2-entries rule applies; it enters in F4 only if Continue or Cursor lands a second entry, otherwise it stays a `WiringStep(kind="manual")`.

**F.3 — what derives.** `_guides` deleted. `connect.py:200` help text derived. `config.example.toml:130-172` (5 ids, a different set), `docs/editor-integration.md` (7 ids, a third set), 4× `SKILL.md` + 4× `references/editor-settings.md` generated between markers by Phase 4's `generate-registry-artifacts.py --check`. `scenarios.py:93-97`'s hardcoded `"claude-code" in user_agent` becomes `{n for s in HARNESSES if s.background_ua_heuristic for n in s.user_agent_needles}` — which is how the `claude-cli` (`config.example.toml:149`) vs `claude-code` (`scenarios.py:97`) vs `claude-code/1.0` (contract vector) three-way split dies. `admin.harness_registry_payload` (`admin.py:238-269`) gains `wire`, `binary_verified`, `known_limitation` — additive, already guarded by `test_admin_harnesses.py:106`.

**F.4 — `connect --toggle` writes the match block.** Today it writes `{"id","enabled","known_id"}` (`connect.py:152`) and no `match`, so a user who runs the one-click flow but never changes `ANTHROPIC_API_KEY` to `netllm-claude-code` falls silently to `default` (`source_identity.py:131`). It now also writes `match.user_agent_contains` from `user_agent_needles` and `scenarios.*` from `scenario_defaults`. **Materialised into `config.toml`, never applied implicitly at runtime**, so `netllm config export` remains the whole truth and Phase 2's merge semantics apply unchanged.

**F.5 — enforcement, three strengths.**

*Static (`tests/conformance/kit_harness.py`, parameterized over `HARNESSES`, ~11 asserts each, zero test-file edits per new harness):* `wire.surface` ∈ the served `Surface` set **and** a route with that surface exists in `routes.json` (made exact-set by Phase 0 item 4 — so deleting `/v1/responses` fails *Codex's* test by name); `base_url_shape` matches the route mount; `requires_streaming ⟹` the path is in `drivers._STREAMING`; icon exists (`test_admin_harnesses.py:46-53` moved into the kit); a UA carrying `needles[0]` resolves to `<id>` through the real `resolve_source`; the virtual key `netllm-<id>` round-trips including the `.<secret>` form; `scenario_defaults` validate as `ScenarioRule` with nameable surfaces; the generated `config.example.toml` block round-trips to a valid `SourceConfig`; the `_guides` parity assert survives as a projection test on the rendered output.

*Behavioural, offline — one golden vector per harness.* New authoring module `tests/contract/scenarios_harness_contracts.py`, fourth peer of the three existing generator modules, materialising `tests/contract/vectors/harnesses/<id>/*.json`. The request body is the harness's real opening request; headers carry the spec's UA needle and virtual key, which `drivers.py:163` already supports with **zero driver changes**. Assertions: status, dialect envelope, `source_counts[<id>] == 1`, `scenario_counts`, and — the joint with Axis G — **every name in `wire.requires_fields` present in the recorded upstream body under `field_map[name]`**. `FakeFarm` already captures the body (`farm.py:115`, stored `:329`); only `inference_calls()` (`farm.py:268-278`) fails to project it. Add an **opt-in** `body_keys` projection read from a per-vector `record_body_keys` flag, so all 146 existing vectors record byte-identically and the zero-diff gate holds.

That last clause is what makes a harness requirement *enforced*: drop `thinking` from `anthropic_bridge.py:21-28` and Claude Code's vector goes red; drop `reasoning` from `openai_responses_bridge.py:30`'s four-name `_PASSTHROUGH_KEYS` and Codex's goes red. It retires `docs/solutions/codex-responses-smoke.md` as a gate.

*Runtime — `connect --verify` and `netllm doctor`.* `--verify` (implied by any wiring write) replays each `VerifyProbe` against the live agent using `commands/diagnose.py`'s existing machinery: agent health; requested model present in `./netllm models`; **surface reachability** at the declared `base_url_shape`+`wire_api` with `max_tokens=1`; streaming (≥2 SSE frames plus a terminal frame in that dialect) when `requires_streaming`; tool-use block when `requires_tool_use`; and the **attribution round-trip** — send with `netllm-<id>`, re-read `GET /netllm/v1/status`, assert `source_requests[<id>]` incremented and `default` did not. PASS/FAIL table, `--json`, non-zero exit on any FAIL. Clauses that did not fire print `not required`, never `PASS`. `doctor` re-runs only the *static* clauses against each configured source with a `known_id`, plus `binary_verified=False` and stale `verified_at` warnings — same predicates, no duplicated logic.

**F.6 — the `responses` surface gap.** `ScenarioRule.surfaces` (`models.py:211`) accepts only `chat|embeddings|messages`; `/v1/responses` delegates to `proxy_chat_completion` (`responses.py:41-43`) and reports `surface="chat"`. A rule scoped to Cursor silently also hits Codex, and a Codex-only rule is inexpressible — the D14 footgun `models.py:198-207` reasoned about for embeddings, left open for the harness the surface exists for. Add `"responses"` to `SurfaceName`, stamp `request.state.surface`, thread it through `classify_scenario`/`applies_to`. This is the **only** F/G item that is not vector-neutral: it takes divergence id **D19**, a `docs/release-notes/` entry, and one minor of `surfaces=["chat"]` aliasing to `{"chat","responses"}` with a `DEPRECATIONS` row. It is isolated in its own phase so it can be cut without touching anything else.

---

### 11. Axis G — upstream change absorption

**The root cause, stated once:** every translation and probe boundary in the router is a hand-maintained allowlist of field names with no counterpart to diff against, and the two most dangerous ones sit inside `netllm_core`, which is correctly forbidden from importing the vendor types that would make a signature diff possible (`tests/test_sdk_isolation.py`). The isolation rule is not relaxed. The counterpart comes from two other places: **the SDK packages, where imports are legal**, and **recorded real transcripts**.

**G.1 — the Anthropic mirror (closes G5 and a 502 firing today).** `client.py:47,64` splat `**payload` — the caller's unfiltered wire body (`surfaces/messages.py:44-55` passes `plan.payload` verbatim). Any Messages field the pinned SDK does not type raises `TypeError` → `_wrap` sets `status_code=None` (`client.py:74-76`) → `app.py:533` → **502**, after burning the whole failover budget across every candidate backend. This needs no SDK bump; it fires now. Ship `packages/netllm-sdk-anthropic/src/netllm_sdk_anthropic/payload.py` as a line-for-line mirror of `netllm_sdk_openai/payload.py`: `_SDK_MESSAGES_PARAMS`, a `_SDK_CONTROL_PARAMS` strip (`extra_headers`/`extra_query`/`timeout` — the F-42 hardening has no Anthropic twin today), and `adapt_messages_payload_for_sdk` splitting untyped fields into `extra_body` (which `AsyncMessages.create` types). Plus `packages/netllm-sdk-anthropic/tests/test_sdk_param_drift.py`, a 3-assert copy of `test_sdk_param_drift.py:24,30,34`. `scripts/ci.sh:48-57` already runs that directory, so it lands in the `sdk` job **and** in the weekly `sdk-canary.yml` with zero workflow edits. ~80 LOC + ~30 test.

**G.2 — `FieldContract`: dispositions and an explicit unknown-field policy.** Replace the bare tuples (`openai_responses_bridge.py:30`, 4 names; `anthropic_bridge.py:21-28`, 6 names) with a declared disposition per field in `netllm_core/wire_contracts.py`:

```python
FieldContract(
  boundary="anthropic->openai",
  dispositions={"model": PASSTHROUGH, "messages": TRANSLATE, "tools": TRANSLATE,
                "thinking": DROP(reason="no openai chat counterpart; extended thinking silently off",
                                 expires="0.6.0"),
                "top_k": TRANSLATE(to="extra_body.top_k")},
  unknown_policy="drop_and_count",   # CHOSEN, not accidental
)
```

Today the unknown-field policy is neither chosen nor written down; both bridges silently drop and return 200. Keep dropping (never 400 on an unknown field) but make it **observable**: one WARN log per `(boundary, field)`, counter `netllm_wire_fields_dropped_total{boundary,field}`, response header `x-netllm-dropped-fields`, surfaced in `doctor` and the dashboard. ~40 lines, no vendor import. Every `DROP` carries `reason` + `expires`; expiry runs on Phase 6's existing `docs/deprecations.toml` clock as a new kind, not a second clock. Erosion gate `scripts/check-wire-allowlists.py` (AST, same technique as `check-engine-erosion.py` + `DECLARED_SEAMS`) fails on any undeclared field-name literal tuple in `netllm_core/*_bridge.py` or `netllm_sdk_*/payload.py`; seeded green with today's four. **Tripwire: >8 `DROP` entries means the policy is wrong — redesign, do not add rows.** Where a vendor type *is* importable, the diff is asserted in the SDK package: `packages/netllm-sdk-openai/tests/test_wire_contract_drift.py` asserts `carried | dropped == set(inspect.signature(AsyncResponses.create).parameters) - control`.

**G.3 — recorded upstream fixtures.** `tests/fixtures/` holds five files today (3 oMLX admin, 2 Anthropic bodies); there is no recorded Ollama, LM Studio, or vLLM body anywhere, and every local test hand-builds `{"data":[{"id":...}]}` (`test_local_discovery.py:73,104`). Ship `scripts/record-upstream.py` with a strict header allowlist and sanitizer, writing `tests/fixtures/upstream/<target>/<date>/{manifest.toml, models.json, chat.json|messages.json, chat.sse}`. `manifest.toml` carries `target`, `server_version`, `captured_at`, the exact capture command, and the sanitizer version. `FakeFarm` gains `replay_fixture(path)` — injected at the existing transport patch point (`farm.py:1-12`), so the real SDK request-build and response-parse paths still execute. `tests/conformance/kit_upstream.py` replays each fixture through `health.status_from_response` and asserts `model_count > 0` — the failure mode that matters, because a `[]` catalog makes a backend a **catch-all candidate for every model** (`test_model_aliases.py:47-60`) and turns one shape change into 404 storms attributed to the wrong backend. `.sse` transcripts are replayed through the real stream pump, the only offline touch on the SSE-framing class. **Start at exactly two targets (ollama, vllm).** LM Studio and oMLX are maintainer-recorded (§G.7).

**G.4 — `validated_at` and the provider canary.** `cloud_providers.py:1-9` dates all five providers with a single module comment ("as of 2026-07-22") that cannot be asserted, expired, or reported. Add `validated_at: str` and `catalog_source: Literal["live","static"]` to `CloudProviderSpec` **and** to `LocalProviderSpec while Phase 3 is writing that file** — this is the one hard sequencing constraint in Axis G; retrofitting costs a nine-entry migration and a second review round, adding them in-place costs minutes. Staleness reuses Phase 6's `compare_versions`/expiry predicate: >180 d **warns in `run_lint`** (never blocks an unrelated PR), fails the canary. `.github/workflows/provider-canary.yml`, weekly + `workflow_dispatch`, copying `sdk-canary.yml`'s `actions/github-script` open/comment block verbatim: per provider with a secret, `GET {base_url}/models` returns 200 and **`static_models ⊆ live catalog`** — the drift `docs/cloud-providers-plan.md:38` concedes and nothing gates. Providers without a secret **skip with a named reason**, never silently green. `zai` has `models_endpoint=False` (`:86`) so its 5-model tuple can never self-heal — that is a permanent ledger row, not a silence. The non-secret half (DNS/TLS reachability of every `base_url`) runs on PRs. Neither job ever gates a PR; file an issue only after **two consecutive** failures; delete any leg that files >1 false issue per quarter.

**G.5 — capability classification override.** `capabilities.py:67` defaults unknown names to `chat` — safe for the chat guards, unsafe for the Phase-4c embeddings guard (`policy.py:127-156`), which 400s `voyage-3-lite`. The documented remedy (`v0.5.0.0.md:19`) is a `[routing.model_aliases]` entry whose *request* name carries an embedding token, and it works — but no test names a model matching **no** heuristic (`test_embeddings.py:74,81-84` deliberately picks one that survives; `scenarios_naming_cloud_guards.py:376-383` uses two token-carrying names and so cannot fail if the guard moved to `effective_model`). Add `[routing.model_capabilities]` as an explicit map consulted before the heuristic; keep the alias path supported; ship one vector naming a no-token encoder and proving **both** escape hatches; add the section to `docs/config-reference.md`, where the word `capability` does not currently appear.

**G.6 — ceilings below the SDKs.** `test_sdk_versions.py:53-69` iterates a hardcoded 2-tuple. `httpx>=0.28` is floor-only yet load-bearing in both SDKs, both probes, `FakeFarm`'s transport patch and `test_messages_stream_f30.py`'s `inner._mounts` reach-in — F-16 one layer down. Replace the tuple with `ledgers/dependency-pins.toml` (`ceiling_required: bool` per runtime dep), add `httpx`/`pydantic` ceilings, and add them as a **separate canary matrix leg** so an httpx break cannot mask an SDK break.

---

### 12. Where F and G insert in the phase plan

**Field additions inside existing phases (≈1 d, mostly free):**

| Phase | Addition | Cost |
|---|---|---|
| **0** | `_guides` ↔ `KNOWN_HARNESSES` parity assert (one line, closes a hard crash — no dependencies, do it first); `mirrors.toml` fact classes `harness-id`, `wire-field`, `virtual-key-prefix`, seeded with every mirror that exists today; seed `ledgers/wire-allowlists.toml` with today's four allowlists; `DEPRECATIONS` gains kinds `harness`, `wire-field`, `provider-fact` | **+0.5 d** |
| **1** | `projections.py` gains a markdown roster-table parser — Axis A's 5 doc rosters need it anyway | **+0 d** |
| **3** | `LocalProviderSpec` gains `validated_at` + `contract_fixtures` **while the file is being written** — the one hard sequencing constraint | **+0 d** |
| **4** | `CloudProviderSpec` gains `validated_at` + `catalog_source`; `generate-registry-artifacts.py` absorbs the harness projections | **+0 d** |
| **6** | G.6 dependency-pin ledger + httpx/pydantic ceilings + canary matrix leg | **+0.5 d** |
| **8** | `docs/extending/06-harness-integration.md`, `07-upstream-absorption.md`, `docs/harness-contracts.md` (generated), `docs/upstream-contracts.md` (the §13 matrix, every row citing its kit test or ledgered open), `tests/extending/test_worked_example_harness.py` | **+1.5 d** |

**New phases:**

| # | Phase | Depends on | Parallel with | Exit criterion | Effort |
|---|---|---|---|---|---|
| **G1** | Anthropic payload mirror + drift test + control strip + `extra_body` split; `wire_contracts.py` `FieldContract`s; unknown-field policy chosen, logged, countered, headered; `check-wire-allowlists.py` in `run_lint` | **Phase 0 only** | 1, 2, 3, 5a | drift test green in `ci.sh sdk`; an untyped Messages field reaches upstream in `extra_body` instead of 502-ing (demonstrated); ledger seeded; 0 vector diff | **2 d** |
| **F1** | `HarnessSpec`; `_guides` deleted; `--toggle` writes needles + scenario defaults; `scenarios.py` literal derived; `kit_harness` structural half | 0, 1 | G2, 2, 3 | `kit_harness` green ×N; help text derived; `netllm connect pi-agent` works; 0 vector diff | **3 d** |
| **G2** | `record-upstream.py`; ollama + vllm fixtures; `FakeFarm.replay_fixture`; opt-in `body_keys` projection; `kit_upstream` | 1 | F1 | fixtures replay through real parse paths; every existing vector byte-identical | **2.5 d** |
| **G3** | `[routing.model_capabilities]` + the no-token-encoder regression vector + `config-reference.md` | 2 | anything | both escape hatches proven by test | **1 d** |
| **F2** | Harness golden vectors with `requires_fields` assertions; `connect --verify`; doctor harness checks | F1, G1, G2, 5a | 6 | ≥1 vector per harness incl. streaming + tool-use; `--verify` exits non-zero on a broken wiring (demonstrated); `codex-responses-smoke.md` retired as a gate; 0 *existing* vector diff | **3 d** |
| **G4** | Staleness clock + `provider-canary.yml` | 3, 4, G1 | 5b, 7 | canary green or skipping with named reasons; clock demonstrated red on a backdated entry | **2 d** |
| **F3** | `SurfaceName += "responses"`, threaded; alias window for `surfaces=["chat"]` | 5a, F2 | 6 | a Codex-only `ScenarioRule` is expressible; declares **D19**; release note written | **1.5 d** |
| **F4** | `connect --write` (deep-merge into declared structured config paths only) — **only if a second `ConfigFileWiring` entry exists** | F2 | 8 | Codex TOML deep-merged, `.bak-<ts>` + unified diff + `--yes`; idempotent; fixture test proves unrelated keys survive; all three never-auto-edit statements edited in the same commit; release-noted | **1.5 d** |

**Added ≈ 19 d** on the adopted 27.5 ⇒ **≈ 46.5 d**. Three-way parallelism after Phase 1: G1 touches only `packages/netllm-sdk-anthropic/` and the two bridge modules; G2 touches only `tests/`; F1 touches `netllm_core/harnesses.py` + `connect.py`. Disjoint file sets, three concurrent branches.

**New coherent stopping points**, added to the program's "after 0, after 3, after 6": **after Phase 0 + G1** (2.5 d — the only two defects in either axis that users can hit *today*: a hard CLI crash and a 502 needing no upstream change) and **after F1 + G2** (both registries and the fixture rail exist; gates can be added incrementally).

**Release notes required:** G1 (the `extra_body` split changes what reaches upstream; requests that 502'd now succeed), G2's drop header/counter, F3 (D19 surface narrowing), F4 (policy inversion).

---

### 13. Which upstream-change classes become CI-detectable — and which do not

**CI-detectable at PR time (offline, no credentials):**
1. Anthropic SDK adds/removes/renames a typed `messages.create` param → `_SDK_MESSAGES_PARAMS` drift test (and one week early via the existing `sdk-canary.yml`, free).
2. A client sends a Messages field the pinned SDK does not type → **fixed, not merely detected**: `extra_body` split. Control kwargs stripped, closing the missing F-42 twin.
3. A field a harness declares in `requires_fields` is dropped by either bridge (`thinking`, `top_k`, `cache_control`, `reasoning`) → harness vector `requires_fields ⊆ recorded upstream body`.
4. A fifth hand-rolled field allowlist lands anywhere → `check-wire-allowlists.py`.
5. An OpenAI Responses/chat/embeddings typed param changes → existing `test_sdk_param_drift.py` plus the new `test_wire_contract_drift.py` in the SDK package.
6. Ollama or vLLM `/v1/models` (or chat, or SSE framing) shape regresses against the recorded fixture → `kit_upstream` replay. Catches the `model_count=0` → catch-all-backend failure.
7. A vendor ships an encoder matching no capability heuristic → caught at registry-entry time by the classification test; the documented remedy is regression-protected.
8. httpx / pydantic / fastapi major → dependency-pin ledger + ceilings.
9. A harness id, virtual-key prefix, or wire field name appears in a new file → `mirrors.toml`.
10. A route a harness depends on is deleted → `kit_harness` fails *that harness's* test by name, via exact-set `routes.json`.
11. A `KnownHarness` added without a guide → structurally impossible after F1; the parity assert covers Phase 0 → F1.

**Weekly, and only where a CI credential exists:**
12. Cloud provider moves `base_url` or changes auth → `provider-canary`. Note 401/403 currently counts as **online** in both probes (`health.py:41-48,264`), so this is invisible to health today.
13. Cloud provider deprecates a `static_models` id → `static_models ⊆ live catalog`. Covers 4 of 5 providers; `zai` (`models_endpoint=False`) is a permanent ledger row.
A canary needs a network and secrets. Forks and external contributors have neither. Every skipped provider prints a named reason; a silent skip is worse than no canary.

**Not detectable by anything proposed here, permanently ledgered in `docs/upstream-contracts.md`:**
14. **A vendor changing SSE event names or framing on a live provider.** `FakeFarm` emits names we authored, and that determinism is load-bearing. The recorded `.sse` fixtures catch *regressions against what we recorded*, not the vendor changing after the recording. Only a live streaming canary against every provider would close this, and it is not worth the cost.
15. **A semantically wrong `base_url`** — right host, wrong region — answers 200 to every probe.
16. **A provider silently re-quantizing or shadow-routing a model.** No wire signature exists.
17. **A harness changing its own wire protocol before we re-record** (the `wire_api=chat` removal class). The fixture staleness clock schedules the re-record; nothing forces it.
18. **Mixed-version mesh × upstream change.** An old peer applies its own `_SDK_MESSAGES_PARAMS`, guards and aliases and fails asymmetrically, attributed to the wrong machine (`policy.py:56-64`; `lifecycle-inventory.md:41,82`). This is Phase 6's `NETLLM_COMPAT_PRETEND_VERSION` lane and is unchanged by F and G.
19. **LM Studio and oMLX shape changes.** Neither runs headless in GH-hosted CI (§G.7).

---

### 14. What stays manual, and why

- **Writing the harness's own config file** — for every harness except any with a fixture-tested `ConfigFileWiring`. The never-auto-edit policy (`known_harnesses.py:23-26`, `dashboard.js:1997-2000`, `SKILL.md:43,57,118`) is defensible; F1/F2 make the *printed strings correct and verified*, which is the plug-and-play win. F4 narrows the policy honestly rather than reversing it, and is cuttable.
- **Recording LM Studio and oMLX fixtures** — maintainer-run via `record-upstream.py`, governed by the same `validated_at` clock. No public headless image exists.
- **Verifying a harness binary name.** Three of six are guesses the code already flags (`known_harnesses.py:45-48`), one (`honcho`) collides with the widely-installed Foreman clone, and `detected` is load-bearing in the CLI, dashboard and macOS badge. `binary_verified` + `verified_at` turn that admission into data with a date; a human still has to install the tool once.
- **Running `connect --verify`.** It needs the user's machine, their harness, and a live agent. CI can prove the *recipe* is internally consistent; only the user can prove it works there.
- **Semantic correctness of any spec entry.** §7 already says structural conformance cannot tell you a base URL is wrong. `confidence: verified|declared` is the honest encoding: `verified` requires a passing golden vector *and* a recorded real opening request. Cursor, Gemini CLI, Buzz, Pi Agent and Antigravity will publish as **declared**, and `docs/harness-contracts.md` will render a smaller verified roster than today's six-row registry implies. That is the truth becoming visible, not a regression — say so in the doc, because someone will read it as one.
- **Doc prose.** Only rosters, wiring blocks and capability tables are generated between markers.

### 15. What is NOT worth doing

1. **A live harness canary** that weekly-installs real CLIs in CI. Claude Code, Cursor and Antigravity cannot be driven headlessly; realistically two of eight harnesses are scriptable, at the price of three third-party npm installs per week in a job with repo credentials. Worst cost-per-risk item proposed in any design.
2. **A capability-token vocabulary and request-path negotiation with a new 400.** At N=8 harnesses a token set will collapse into one token per quirk — `_guides` with type annotations — and enforcing declarations sourced from prose the map has already proved wrong three ways would refuse real traffic. The declarative `WireRequirement` earns its place because it feeds an offline assertion; an abstract capability algebra does not. `FieldContract`'s dispositions give the same "which layer eats this field" answer with no new vocabulary.
3. **Generating dashboard JS or SwiftUI for harnesses.** §6 item 3 already settled this. Generate the manifest of what must exist.
4. **Auto-editing shell profiles.** Ever. `env` steps stay printed.
5. **A second deprecation/staleness clock.** `docs/deprecations.toml` (Phase 6) gains kinds; it does not get a sibling.
6. **Recording fixtures for all nine backends up front.** Two targets, then measure whether anyone refreshes them.
7. **`_HarnessGuide.surface` preserved in any form.** Its three values (`openai|anthropic|codex`) have **zero** overlap with `get_args(SurfaceName)`; it is a display string in a stale namespace and is deleted, not migrated.

### 16. Cut line — if only 5 days exist for F and G combined

| Ship | d | Why it survives |
|---|---|---|
| Phase-0 slice: `_guides` parity assert + `harness-id`/`wire-field` mirror classes + `wire-allowlists.toml` seed | 0.5 | One line stops a hard `KeyError` in the primary onboarding command; the ledger blocks a 12th restatement of the roster during the highest-churn phases |
| **G1 full**: Anthropic `_SDK_MESSAGES_PARAMS` + control strip + `extra_body` split + drift test; `unknown_policy` written down; drop log + counter | 2.0 | The only work that fixes a defect firing **today** — any untyped Messages field is a 502 after the whole failover budget burns. Lands in `ci.sh sdk` and `sdk-canary.yml` with zero workflow edits |
| **F1-lite**: `HarnessSpec` with `wire` + `user_agent_needles`; `_guides` deleted; `connect` rendered from spec; `--toggle` writes the match block. **Generation deferred** — the mirror ledger already tracks divergence | 1.5 | Kills the shadow roster and the silent-attribution hole; without it the vectors would guard a mess |
| **Two harness vectors**: Claude Code (Messages at port root, streaming, tools, `thinking`) and Codex (`/v1/responses`), using the opt-in `body_keys` projection | 1.0 | The two harnesses that actually caused router code to be written; the only thing that would have caught the `wire_api=chat` removal |
| **Total** | **5.0** | |

Also add, at zero marginal cost because those files are being written anyway: `validated_at` on `LocalProviderSpec` (Phase 3) and `CloudProviderSpec` (Phase 4). Two fields now; a nine-entry migration later.

**Deferred at 5 days, in the order they come back:** the other six harness vectors → `connect --verify` → G2 fixture corpus → G3 capability overrides → G4 provider canary → doc/config generation → F3 `SurfaceName` → F4 `--write`.

**Refuse to cut at any budget:** the `_guides` parity assert, the `_SDK_MESSAGES_PARAMS` drift test, and `unknown_policy` being explicitly chosen and written down. Under one day between them, and they are the difference between failures that are loud and failures that are invisible.

### 17. Risks carried forward (extending §9)

- **R7 — the harness registry becomes validated fiction.** Generation would launder three unverified binaries and two harnesses with no confirmed working wiring into apparent fact. Mitigated by `binary_verified`/`verified_at`, `confidence`, `known_limitation` with an expiry, `--verify` printing `not required` (never `PASS`) for clauses it did not run, and a generated doc that renders declared-vs-verified as separate columns. This is R3 in its sharpest form.
- **R8 — the `DROP` ledger legitimises dropping.** Making a silent drop declared is a real gain that also makes declaring comfortable. `expires` is the only defence and expiries get bumped; the >8-entry tripwire is the backstop.
- **R9 — canaries get muted.** Cron + issue only, never a PR gate; two consecutive failures before filing; skips name a provider and a reason; delete a leg that files >1 false issue per quarter.
- **R10 — fixtures rot into a second source of truth.** A fixture is a **baseline for a diff**, never an assertion on its own. `captured_at`/`server_version`/`command` in every manifest; staleness fails the canary, warns in lint.
- **R11 — test-infrastructure mass half-built.** This adds two kits, one recorder, one workflow and a fixture corpus to a suite that runs in ~12 s. Every gate ships ledger-seeded green on day one (the Phase-0a pattern); the corpus starts at exactly two targets; each kit must be green with zero entries wired before any entry is added.