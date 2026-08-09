# Lifecycle & Standards Pathways Inventory — llm-swarm-router @ 243e3dc (v0.5.0.1)

Read-only audit. No files written. Verdict up front: **the SDK-update pathway is genuinely well engineered (docs + tests + CI + canary + Dependabot), the API-versioning pathway is a habit rather than a policy with a stale enforcement test, and the config-evolution pathway is the only one with an actively destructive failure mode.** There is no mesh-upgrade story at all.

---

## 1. The matrix

Legend: ✅ full · ◐ partial · ✗ absent

| # | Pathway | Documented? | Tested? | Enforced in CI? |
|---|---------|-------------|---------|-----------------|
| **A1** | HTTP API compatibility promise | ◐ one line: `docs/platform-matrix.md:24` "HTTP API contract at `:11400`: additive changes only" | ◐ `tests/test_contract.py:16-33,189-195` — presence-only, **stale** (17 of ~28 routes) | ◐ same test, gap below |
| **A2** | API deprecation window / sunset process | ✗ nothing anywhere | ✗ | ✗ |
| **A3** | Additive-endpoint convention | ◐ per-endpoint docstrings: `app.py:321-322`, `packages/netllm-agent/AGENTS.md:42` | ✅ `tests/test_admin_harnesses.py:106` (harnesses only) | ✅ via that test |
| **A4** | Mesh version drift detection | ✅ `packages/netllm-agent/AGENTS.md:36`, `docs/architecture/08-feature-integration-status.md:111`, `docs/routing-hardening-plan.md:241` | ✅ `tests/test_routing_hardening.py:195-218` | ✅ in `tests/` |
| **A5** | Old-peer wire tolerance | ◐ docstring only | ◐ `tests/test_contract.py:269-297` (heartbeat backend rows only) | ◐ |
| **B1** | Config schema versioning | ✗ — config.toml carries **no** version key | ✗ | ✗ |
| **B2** | Old config → new agent | ✅ implicitly (pydantic defaults) | ✅ `tests/test_contract.py:52,65,117,127` | ✅ |
| **B3** | New config → old agent (downgrade / mixed mesh) | ✗ | ✗ | ✗ — **silently destructive, see G1** |
| **B4** | Config key rename/removal migration | ✗ no migration module, no `netllm config migrate` (`commands/config_io.py` = export/schema/import only) | ✗ | ✗ |
| **B5** | Config deprecation convention | ◐ ad-hoc per-field comments: `models.py:296-300`, `pool.py:394-396` ("kept for one release"), `07-findings-register.md:542-573` | ✗ no expiry check | ✗ |
| **B6** | Merge semantics (F-01) | ✅ excellent — `config_merge.py:1-30`, `docs/config-guards-audit.md` | ✅ `tests/test_config_merge.py` | ✅ |
| **B7** | Write guards (F-02) | ✅ `config_guards.py:1-19` | ✅ | ✅ |
| **B8** | Client-side schema degradation vs older agent | ✅ designed: `docs/config-schema-rewrite-plan.md:302-305`, `config_schema.py:44-46` | ◐ `tests/test_config_schema.py:80` tests the constant | ✗ — **constant is dead, see G10** |
| **B9** | New Python field ⇒ conscious client decision (F-21) | ✅ prescribed `07-findings-register.md:625-641` | ✗ `test_config_schema.py:28` is auto-satisfied (schema is *generated* from the models) | ✗ |
| **C1** | Telemetry wire schema versioning | ✅ `docs/telemetry-api.md:30,62` | ✅ `tests/contract/test_telemetry_contract.py:49`, `tests/test_telemetry.py:32` | ✅ |
| **D1** | SDK pin policy + bump checklist | ✅ `docs/sdk-versions.md:11-84`, `CONTRIBUTING.md:168-176` | ✅ `tests/test_sdk_versions.py:36,41,48-69` | ✅ `scripts/ci.sh:45-56`, `ci.yml:62-67` |
| **D2** | SDK param-set drift (OpenAI) | ✅ `payload.py:20-72` comments | ✅ `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py` | ✅ `sdk` job |
| **D3** | SDK param-set drift (**Anthropic**) | ✗ | ✗ | ✗ — **G5** |
| **D4** | SDK isolation (core must not import vendor SDKs) | ✅ `docs/sdk-versions.md:5` | ✅ `tests/test_sdk_isolation.py` | ✅ |
| **D5** | Upstream drift early warning | ✅ `docs/sdk-versions.md:64-71` | — | ✅ `.github/workflows/sdk-canary.yml` (weekly cron), `.github/dependabot.yml:34-59` |
| **D6** | New cloud-provider version discipline | ◐ `docs/cloud-providers-plan.md:65`, `cloud_providers.py:70` (prefer live `/v1/models`) | ✗ no per-provider validated-date or canary | ✗ |
| **E1** | Version numbering scheme (4-part `X.Y.Z.N`) | ✗ never stated; no semver claim | ◐ `update.py:47-61` digit-extraction comparator, `tests/test_update.py:20` | ◐ |
| **E2** | Version sync across workspace | ✅ `docs/ci-and-release.md:69-70` | ✅ `tests/test_version_sync.py:38-63` | ✅ |
| **E3** | Version sync: Homebrew / winget | ✗ | ✗ | ✗ — **G7** |
| **E4** | Release runbook | ✅ `docs/ci-and-release.md:66-84` | ✗ | ◐ `release.yml` builds/attaches artifacts |
| **E5** | Release-notes discipline | ✅ strong — 23 files in `docs/release-notes/`, `ci-and-release.md:72`, `refactor/RELEASE-NOTES.md` (annotated behavior changes D2…D19), `plan-f24-f26.md:129` gates merge on it | ✗ nothing asserts a notes file exists for the current version | ✗ |
| **E6** | macOS in-app updater | ✅ `docs/macos-install.md:120-129`, `platform-matrix.md` | ◐ `tests/test_update.py` (Python side); Swift `UpdateController` separately | ◐ `menubar-lifecycle` job |
| **E7** | Package channels (deb/rpm/zip/DMG/winget) | ✅ `docs/platform-matrix.md`, `packaging/README.md` | ✅ `tests/test_packaging_version.py` (RPM version mapping) | ✅ `packaging-smoke` job |
| **E8** | **Mesh upgrade (mixed versions across machines)** | ✗ | ✗ | ✗ — **G8** |

---

## 2. What actually works well

- **`peer_config_warnings()` exists and is wired end-to-end.** `packages/netllm-agent/src/netllm_agent/service/status.py:64-89` compares strategy and version against every live peer; the result lands in `status_payload` as `peer_warnings` (`status.py:57-59`), in doctor notes (`admin.py:151`), and renders in the dashboard (`static/dashboard.js:536-539`). Tested at `tests/test_routing_hardening.py:195-218`. The `version` field is genuinely plumbed through all three transports: mDNS TXT (`mdns.py:141`), heartbeat (`swarm_tasks.py:67`), status fetch (`swarm.py:161`, published `swarm.py:265`).
- **The SDK bump pathway is the model the other axes should copy**: a pins table with last-validated dates, a 3-layer change classifier, a 6-step checklist, floor+ceiling enforcement (F-16, `test_sdk_versions.py:48-69`), a signature-drift test, weekly Dependabot with an `sdk-bump` label, and a weekly canary that files a GitHub issue on failure.
- **Config *merge* is the best-documented subsystem in the repo** (`config_merge.py:1-30` explains all three merge behaviors and why each exists). This is the F-01 fix and it holds.
- **Telemetry is the one wire contract that is actually versioned** (`telemetry.py:272` `schema_version: 1`) and contract-tested.

---

## 3. Gaps, in the order they will bite

### G1 — Saving config on an older agent **silently deletes** newer keys (operator, severity: highest)
`packages/netllm-core/src/netllm_core/models.py:481-487` declares no `model_config`, so pydantic's default `extra="ignore"` applies. `load_config` (`models.py:657-664`) drops unknown sections/keys; `save_config` (`models.py:677-690`) then rewrites the *entire* file from `model_dump()`. Verified empirically: a `[future_section]` and an unknown `agent.future_field` both vanish.

Every write path triggers this — `POST /netllm/v1/admin/config` (`app.py:387`), `netllm config import` (the macOS Settings **Save** button), `netllm join`. Concretely: an operator with a 5-machine mesh upgrades one box, configures a new provider there, then opens Settings on a not-yet-upgraded box and hits Save — the newer keys are gone from that machine's config with no warning, no backup, no test, no doc. This is the exact class of bug F-01 was filed for, one level up (fields vs. whole sections). Nothing in `tests/` covers unknown-key round-trip.

### G2 — The "additive changes only" promise is unenforced for the newest third of the API (contributor, axis C)
`tests/test_contract.py:16-33` freezes 17 routes; `app.py` registers roughly 28. Absent from the frozen set: `/v1/responses` (`app.py:468`), `/netllm/v1/telemetry` (`:239`), `/netllm/v1/config/schema` (`:299`), `/netllm/v1/harnesses` (`:317`), `/netllm/v1/cloud/providers` + `/{provider_id}/models` (`:309,326`), `/netllm/v1/logs` (`:362`), `/netllm/v1/admin/config` (`:387`), `/netllm/v1/admin/drain` (`:340`), `/netllm/v1/admin/peers-scan` (`:414`). Deleting the entire Responses surface is CI-green today. The test is also presence-only (`for path in EXPECTED: assert path in paths`), so it can never detect a *removal* outside its literal.

Note `tests/contract/test_api_surface.py` is a **Python module** surface freeze (the 20 `AgentService` attributes `app.py` consumes), not an HTTP freeze — the two names invite confusion.

### G3 — No config schema version, so a downgrade cannot even be *detected*
Telemetry got `schema_version` (`telemetry.py:272`); config did not. `config_schema.py:164` emits `"version": get_version()` but that is the *agent* version used as an ETag/cache key (`docs/architecture/05-configuration-and-control-plane.md:117-129`), not a schema generation. Without a monotonic schema number there is no place to hang G1's guard, no compatibility statement to make, and no way to write a migration.

### G4 — "Kept for one release" is a convention with no clock and no enforcement
Two live examples: `models.py:296-300` (`require_same_model_for_shard`, shim added in **0.4.6** per its own comment, still present at **0.5.0.1** — two minors past its window) and `pool.py:394-396` (`cached_peer_online`, "kept for one release"). `07-findings-register.md:589` prescribes the same pattern for the `config.py` re-export shim (still open as F-55). There is no deprecation policy doc, no `DeprecationWarning` at load, and no CI check that an expired shim is removed. A contributor has no way to know when it is safe to delete one.

### G5 — Anthropic has none of the OpenAI SDK's drift protection (contributor, axes A/B)
`netllm_sdk_openai/payload.py:20-72` mirrors the pinned SDK's typed param sets and `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py` fails loudly when they diverge (F-36). `netllm_sdk_anthropic/client.py:44,58` instead splats `**payload` straight into `messages.create`, so there is no mirror to drift — but equally no extension-field split into `extra_body` and, per `docs/closure-roadmap-2026-08-03.md:37`, no F-42 control-kwarg stripping (`extra_headers`/`extra_query`/`timeout` are stripped only on the OpenAI path, `payload.py:16`). An `anthropic` bump lands with only `test_sdk_versions.py`'s lockfile equality as a signal. `packages/netllm-sdk-anthropic/tests/test_client_contract.py` has 5 behavioural tests and no signature assertion.

### G6 — The SDK doc points at a module that no longer exists
`docs/sdk-versions.md:41` routes "Layer 3 — Agent" changes to `packages/netllm-agent/src/netllm_agent/service.py`. F-24 dissolved that file into `service/` (16 modules). The bump checklist a contributor is told to follow sends them to a dead path on step 4. Nothing tests doc file references — and this is the *first* doc a new-provider contributor is pointed at.

### G7 — Package-manager version literals are outside the sync test
`tests/test_version_sync.py:38-63` covers every workspace `pyproject.toml`, `_FALLBACK_VERSION`, `app.py`'s FastAPI version, and the CLI `__version__` — but not `Formula/netllm.rb:6` (currently correct at 0.5.0.1, bumped manually on `homebrew/*` branches per `docs/closure-roadmap-2026-08-03.md`, with **no** job in `.github/workflows/release.yml`) and not `packaging/windows/winget/netllm.yaml:3`, which is **stale at 0.2.3.2**. The winget file is regenerated at release time (`release.yml:156` → `packaging/scripts/update-winget-manifest.ps1`) and attached as an artifact, so the checked-in copy is a template — but it reads as a live manifest and nothing says otherwise.

### G8 — There is no mesh upgrade story (operator, axis E)
Every `upgrade` hit in `docs/` is single-machine (`macos-install.md:33,87-93`, `macos-troubleshooting.md:18-28,71-92`, `docs/AGENTS.md:32`). Nothing documents: upgrade ordering (gateway before peers, given `swarm_tasks.py:28-52` makes the gateway authoritative for routing strategy), how many versions of skew are supported, what to do when `peer_warnings` fires, or whether a rolling upgrade is safe at all. `commands/join_swarm.py:140` tells the operator to "Verify both machines run a compatible netllm version" without ever defining *compatible*. The one wire-level mixed-version test is `tests/test_contract.py:269` — heartbeat backend rows, v0.3.x shape only; nothing tests an old client against a new agent on `/v1/*` or `/netllm/v1/status`.

Two smaller defects inside the drift warning itself (`status.py:84-89`): it is exact string inequality, so 0.5.0.0↔0.5.0.1 warns as loudly as 0.3↔0.5 despite `update.compare_versions` (`update.py:47-61`) being available for ordering; and the message always says "**update the older machine**" regardless of which side is older, so on the newer agent it is actively misleading.

### G9 — Adding a config field still ships an uneditable field on macOS (contributor, axis D)
F-21 (`07-findings-register.md:625-641`) prescribes a drift test asserting every `NetllmConfig` field is either schema-rendered or in an explicit `KNOWN_UNRENDERED` allowlist. Not implemented. `tests/test_config_schema.py:28` (`test_every_pydantic_field_has_a_schema_entry`) is auto-satisfied because `config_schema.py:33-41` *generates* the document by walking the same models — it can never fail. `routing`'s non-`model_pools` fields and all of `cloud` remain hand-typed Swift structs (`apps/netllm-mac/AGENTS.md:39`). So the three control surfaces drift silently, which is precisely axis D's requirement.

### G10 — `BOOTSTRAP_SECTIONS` is a dead constant
`config_schema.py:44-46` defines the documented fallback for a newer client hitting a pre-schema-endpoint agent (`docs/config-schema-rewrite-plan.md:302-305`). No client imports it: `dashboard.js:276-287` hand-rolls per-section literal fallbacks (which *do* work), and `apps/netllm-mac/Sources/Config/ConfigStore.swift:40-44` reaches the schema through the **bundled** CLI, so the macOS app can never be version-skewed from its agent. The constant is tested (`test_config_schema.py:80`) and used by nothing — a documented pathway that exists only on paper.

### G11 — Two independent "is this newer" implementations, no shared vectors
Python: `update.compare_versions` (`update.py:47-61`), regex digit extraction with zero-padding. Swift: `release.version.compare(currentVersion, options: .numeric)` (`UpdateController.swift:87,232`). Different algorithms, no shared test corpus. Python's also mis-orders prereleases (`1.0.0-rc.1` → `[1,0,0,1]` > `[1,0,0]`); mitigated today only because `fetch_latest_release` drops prereleases (`update.py:~115`, tested at `tests/test_update.py:81`), so an operator *running* a prerelease build is the exposed case. Also `apps/netllm-mac/Sources/AppView/SettingsCards.swift:154` carries a hardcoded `"0.2.1"` version fallback.

---

## 4. Cross-cutting read

The project has **excellent per-change discipline** (release notes enumerate annotated behavior deltas; the contract corpus + `scripts/check-engine-erosion.py` guard the engine seam; F-numbers thread findings → plans → tests) and **almost no over-time discipline**. Every mechanism that exists answers "did this change break something *now*"; nothing answers "what did we promise, for how long, and how does a peer/config/client from six months ago behave".

The single highest-leverage fix for the stated goal — modular, cleanly-expandable CLI and API integration — is a **schema/wire version number plus a stated support window**, because G1, G3, G4, G8 and A2 all collapse into "there is no generation counter to reason about". The second is **extending `EXPECTED_HTTP_ROUTES` to a generated-and-asserted full route set** (G2), which is what makes axis C's "does the SurfaceAdapter protocol absorb a new surface" question answerable in CI rather than by inspection.

---

### Key file references
- `packages/netllm-core/src/netllm_core/models.py:481-487, 657-664, 677-690` (config load/save, G1)
- `packages/netllm-agent/src/netllm_agent/service/status.py:64-89` (peer drift warnings)
- `packages/netllm-agent/src/netllm_agent/app.py:239-434, 438-510` (route registrations vs. frozen set)
- `tests/test_contract.py:16-33, 189-195, 269-297` (route freeze + legacy heartbeat)
- `packages/netllm-core/src/netllm_core/config_schema.py:44-46, 164` (dead bootstrap constant, cache-key version)
- `packages/netllm-sdk-openai/src/netllm_sdk_openai/payload.py:16-72` + `packages/netllm-sdk-openai/tests/test_sdk_param_drift.py` (the discipline to replicate)
- `packages/netllm-sdk-anthropic/src/netllm_sdk_anthropic/client.py:44, 58` (the gap)
- `docs/sdk-versions.md:11-14, 41, 46-60`; `docs/platform-matrix.md:22-24`; `docs/ci-and-release.md:66-84`
- `tests/test_version_sync.py:38-63`; `Formula/netllm.rb:6`; `packaging/windows/winget/netllm.yaml:3`