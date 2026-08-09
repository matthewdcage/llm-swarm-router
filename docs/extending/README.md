# Extensibility and hardening program — 2026-08-08

Baseline: `main` @ `243e3dc` (v0.5.0.1) · `uv run pytest -q` → **1113 passed, 4 skipped** ·
`./scripts/ci.sh lint` exit 0.

## Why this exists

The F-24/F-25/F-26 consolidation fixed *fragmentation of the request path*: one failover
engine, one stream pump, one model matcher, both 2 kLOC modules dissolved. It did not
address the other kind of fragmentation — **the cost of adding something new**. Both audits
kept producing the same defect shape (`D17`, `F-35`, `F-45`): a fact added to one surface
and forgotten in its siblings, with a green build either way.

This program targets that class directly, so that adding a provider, a surface, a CLI
command, or a config field is a **documented, tested, proven pathway** rather than an
archaeology exercise.

| Document | Contains |
|---|---|
| [PROGRAM.md](PROGRAM.md) | The adopted program: per-axis contracts, phases, what is *not* worth doing |
| [extension-cost-map.md](extension-cost-map.md) | Measured cost of each extension axis today, with `file:line` for every duplicated fact |
| [lifecycle-inventory.md](lifecycle-inventory.md) | Versioning, deprecation, migration, release and mesh-upgrade pathways — documented? tested? enforced? |
| [harness-integration-map.md](harness-integration-map.md) | Axis F evidence: per-harness detected/attributed/wired/routed/capability-modelled/tested matrix |
| [upstream-absorption-map.md](upstream-absorption-map.md) | Axis G evidence: upstream-change class x how and when it is detected, ranked by "silently reaches production" |
| [08-control-parity.md](08-control-parity.md) | Axis D as built: why the literal per-field descriptor was measured, failed PROGRAM.md's own >20% tripwire and was redesigned; the four field dispositions; the day-one numbers and their denominators; which gaps were closed and which are correctly absent |

Method: two parallel mapping agents measured the real cost against landed commits, three
competing designs were produced independently, and a judge panel scored and synthesized
them. Scores — **registry-first 8.5** (adopted), contract-kit 8.0, plugin-boundary 6.0.

## The single rule

> A fact is stated **once**, in a frozen spec dataclass in a registry. Everything else is
> **derived at runtime**, **generated at build time** with `--check` in lint, or
> **projection-tested**. Anything in none of those three categories is a mirror, and the
> build fails on it.

Every mechanism this uses already exists in the tree — `CloudProviderSpec`, the
`admin.cloud_provider_registry_payload()` runtime projection, `generate-dashboard-tokens.py
--check`, `check-engine-erosion.py`, and the `allowed-divergences.txt` declare-or-fail
ledger. Nothing new is invented where something proven already works.

## Start here — the guides

One per axis. **Every checklist row names the test that fails if you skip it,
or is marked *unguarded*.** There is no third category.

| Guide | Axis | Registry entry + hand-written companions | Kit |
|---|---|---|---|
| [01-cloud-provider.md](01-cloud-provider.md) | A — cloud provider | 1 + **3** | `tests/conformance/kit_cloud.py` |
| [02-local-provider.md](02-local-provider.md) | B — local inference server | 1 + **3** | `tests/conformance/kit_local.py`, `tests/test_contract.py` |
| [03-api-surface.md](03-api-surface.md) | C — wire dialect | 1 spec + 1 adapter | **none** — `tests/contract/test_surface_adapters.py` |
| [04-cli-and-control-plane.md](04-cli-and-control-plane.md) | D — CLI / control | 1 descriptor + per-surface UI | `tests/conformance/kit_config_surfaces.py` |
| [05-config-and-wire-evolution.md](05-config-and-wire-evolution.md) | E — config + wire | additive only today | `tests/test_config_forward_compat.py` |
| [06-harness-integration.md](06-harness-integration.md) | F — external CLI agent | 1 + **1 shadow roster** | **none** |
| [07-upstream-absorption.md](07-upstream-absorption.md) | G — upstream change | — | **none** |
| [08-control-parity.md](08-control-parity.md) | D, as built | — | `tests/conformance/kit_config_surfaces.py` |

Copy-paste stubs: [templates/](templates/README.md).
As-built contract record: [../architecture/11-extensibility-contracts.md](../architecture/11-extensibility-contracts.md).
Version promises and what is actually enforced: [../compatibility-policy.md](../compatibility-policy.md).

## What "proven" means here

`tests/extending/test_worked_example_local.py` and
`test_worked_example_cloud.py` inject a **fixture registry entry** into the
live registry and drive it end to end — discovery URLs → config validation →
schema document → projection endpoint → CLI listing → dashboard payload.

The claim they discharge is **not** [PROGRAM.md](PROGRAM.md) §8's "with zero
source edits beyond the registry entry". That was measured against this tree
and is false. It is:

> zero source edits beyond the registry entry **and its declared
> hand-written companions**, where every companion is enumerated with the
> reason it is hand-written.

Three properties, because one is not enough:

1. **Sufficiency** — the entry plus its declared companions, and nothing
   else, passes every stage. A *new* hand-edit becoming necessary fails the
   stage that needs it, by name. **This property is exactly as strong as the
   stage list**: it can only fail on a fact some stage reads. A surface no
   stage reads is invisible to it — which is how Axis B's third companion
   (`SettingsViewModel.providers`) went undeclared for a phase while its own
   guard, `tests/test_contract.py::test_swift_default_providers_match_python`,
   sat outside every guide checklist. When you add a companion, add the stage
   that reads it.
2. **Necessity** — omitting any companion must break something, so the list
   cannot rot into a pessimistic over-statement.
3. **Classification** — every mirror `tests/conformance/ledgers/mirrors.toml`
   allows must be classified as a companion, a generated block, or a
   capability branch. A new ledger row fails until the worked example *and*
   its guide say which.

## The four gates, and what each error message means

| Gate | Fires when | What to do |
|---|---|---|
| `scripts/check-registry-mirrors.py` | a registry id literal appears in a file the ledger does not name | derive it, generate it, or projection-test it. A ledger row is the last resort and needs a `reason` + `expires` |
| `scripts/generate-registry-artifacts.py --check` | a generated block is stale | run the command the error prints |
| `scripts/check-engine-erosion.py [--seams]` | the failover loop learned about a surface, or a `Surface` branch escaped `service/surfaces/` | move the fact onto `SurfaceSpec` or a `SurfaceAdapter` member |
| `scripts/check-doc-paths.py` | an instructional doc points at a path that does not exist | fix the path. `docs/extending/` and `docs/architecture/` are exempt because they quote future and dead paths on purpose |

## Data, hook, or adapter?

| Question | Answer |
|---|---|
| Do **≥2** entries set it non-default? | a **spec field** |
| Is it one entrant's quirk? | a **hook callable** on the spec — never squeezed into data |
| Is it a wire-dialect behaviour? | a **`SurfaceAdapter`** member |
| Is it a per-surface *widget*? | **hand-written UI**. Generate the manifest of what must exist, never the UI |
| Is it a genuine capability difference (oMLX's admin API)? | a **literal is allowed**, ledgered with that reason, and it must stay the only one |

Review the spec shape at every **third** entrant.

## Axes F and G — added after the first pass under-scoped them

The original program's "Axis D" covered adding a *netllm CLI command*; it did **not** cover
integrating an **external CLI agent** into the router, which is the more strategic axis. It
also deferred SDK-drift hardening. Both are now designed in [PROGRAM.md](PROGRAM.md)'s
addendum (spec-registry 8.6 adopted over canary-contract 7.7 and capability-negotiation 6.4).

**The harness extension path has an unguarded second roster.** `connect.py:225` validates a
harness against `KNOWN_HARNESSES`, then `connect.py:241` indexes a *separate* hand-written
`_guides()` dict. The two rosters happen to agree today, so nothing is broken — but
`grep -rn "_guides" tests/` returns **zero**, and a registry-only addition raises `KeyError`
(reproduced). The identical guard already exists for the icon convention at
`test_admin_harnesses.py:37-38`; it was simply never applied here. One assertion closes it.

**No harness has a machine-readable requirement.** Claude Code needs the Messages surface
and streams by default; Codex needs `/v1/responses` because it dropped `wire_api=chat`.
Both facts live only as English prose, in `connect.py`'s guide dict and
`editor-integration.md`. A routing change that breaks a harness produces a runtime failure,
not a red test. Axis F makes the requirement a declared field with a conformance kit.

## Two findings worth acting on before any refactor

**1. Config writes silently destroy unknown keys — reproduced.**

`NetllmConfig` declares no `model_config`, so pydantic's `extra="ignore"` applies, and
`save_config` rewrites the whole file from `model_dump()`. I reproduced it directly:

```
future_section preserved?       False
agent.future_field preserved?   False
extra policy: (pydantic default = ignore)
```

Every write path — the dashboard **Save**, macOS Settings **Save**, `netllm config import`,
`netllm join` — drops any key that agent doesn't know about. On a mixed-version mesh this
is data loss in the ordinary upgrade path: upgrade one machine, configure a new provider
there, press Save on an older machine, and the new keys are gone. This is F-01's class
(*"saving config silently drops three fields"*) generalized to *every* unknown key.
Roughly 30 lines to fix, and it is what makes rolling upgrades safe at all.

**2. One silent, load-bearing hardcode in the macOS app.**

`PythonRuntime.swift:79-85` holds a closed `(keychain account, env var)` list. A cloud
provider added everywhere else still renders in Settings, accepts a key, and stores it —
but the key is never injected into the agent subprocess, so the provider 401s with a
credential the user can see saved. `api_key_env` is already on the wire at `admin.py:232`,
so this is derivable today. No Swift test covers it.

## The measured picture

| Axis | Files to add one | Duplicated facts | Enforcement | Verdict |
|---|---|---|---|---|
| **A** cloud provider | 13 (measured, `08946b6` DashScope) | 8 fact-classes | 3 roster tests trip; Swift + docs unguarded | Medium friction, one sharp edge |
| **B** local backend | ~14 (no example exists) | ~~11 parallel maps~~ → **1 registry** | ~~zero tests~~ → `kit_local` (Phase 3) | **RESOLVED** |
| **C** API surface | 1–3 for a dialect bridge | 4 escaped `Surface` branches | real AST gate in CI | **Cleanest by a wide margin** |
| **D** CLI + control plane | 32 (measured, `bf67238`) | 3-surface parity manual | per-surface tests; **no parity test** | High volume, mostly genuine work |
| **E** config evolution | 1–6 | 2 hand-maintained allowlists | strong schema tests; **no version, no migration** | Structurally missing |

Axis C scoring cleanest is the consolidation paying off — the `SurfaceAdapter` seam does
absorb new work. Axis B is the inverse: eleven maps keyed on the same provider id, and not
one test referencing the roster.

## What has actually landed (2026-08-09)

The phase table below is the *plan*. This is the tree.

| Phase | State |
|---|---|
| 0 — mirror gate + cheap fixes | **landed** |
| 1 — conformance-kit skeleton | **landed** |
| 2 — config write safety | **landed** |
| 3 — `LocalProviderSpec` | **landed** |
| 4 — Axis A close-out + generation rail | **landed** |
| 5a — Axis C branch absorption | **landed** |
| 5b — `app.py` → `routes/` | **not landed** |
| 6 — versioning, migration, mesh | **not landed** |
| 7 — `ControlDescriptor` + parity | **landed** |
| 8 — docs, DOX, worked examples | **landed** (this) |
| F1–F4, G1–G4 (addendum) | **none landed** |

Consequences a reader will otherwise trip over:

- There is no `versioning.md`, no `mesh-upgrade.md` and no `deprecations.toml`
  in `docs/`. Phase 6 was to write them. What exists instead is
  [../compatibility-policy.md](../compatibility-policy.md), which states the
  promises **and** marks which are enforced.
- `tests/conformance/ledgers/mirrors.toml`'s `current_phase` is `phase-5a`
  and cannot advance past it, because 5b and 6 have not landed. Ledger rows
  expiring at `phase-8` are therefore **not** overdue. The clock is honest; it
  measures refactor phases, not calendar time.
- Guides [05](05-config-and-wire-evolution.md),
  [06](06-harness-integration.md) and [07](07-upstream-absorption.md)
  describe substantial machinery that **does not exist**. Their checklists
  mark those rows ***not built*** and name the phase that would build them.

## Phasing

~27.5 days total, but the value is front-loaded and there are honest stopping points.

| Phase | What | Effort |
|---|---|---|
| **0** | Mirror gate + 9 cheap independent fixes | 1.5 d |
| **1** | Conformance-kit skeleton (no production file touched) | 1 d |
| **2** | **Config write safety** — the data-loss fix | 2 d |
| 3 | Axis B: 11 maps → one `LocalProviderSpec` | 3 d |
| 4 | Axis A close-out + generation rail | 3 d |
| 5a/5b | Axis C: branch absorption, then `app.py` → `routes/` | 6 d |
| 6 | Versioning, migration, mixed-version mesh | 4 d |
| 7 | Axis D: `ControlDescriptor` + the first cross-surface parity gate | 4 d |
| 8 | Docs, DOX contracts, worked-example tests | 3 d |

**Phases 0 and 2 alone — 3.5 days — close both findings above.** Coherent stopping points
after 0, 3, and 6. No axis is ever half-migrated.

Every phase gates on **zero contract-vector diffs**, so a revert is provably a no-op on the
request path.

## What this program refuses to do

Recorded because saying no is most of the value:

- **No plugin boundary.** Designing a 1.0 public API with ~zero third-party consumers costs
  6–11 extra days and opens an unsandboxed in-process code path in a process holding cloud
  keys and a LAN port. The registries are already the right shape to expose *if* a third
  party ever appears — deferring costs almost nothing.
- **No generated SwiftUI or dashboard JS.** ~1100 of `bf67238`'s 1268 lines were genuine
  per-surface UI work. Generate the *manifest of what must exist*; hand-write the UI.
- **No rewrite of `dashboard.js` or `SettingsWindowView.swift`** — 3004 and 1247 lines respectively (`wc -l`, 2026-08-09; both still growing).
  Acknowledged debt; the parity gate makes their *gaps* loud without touching their *size*.
- **No opening of the `Literal` types** to validated `str` — a real typing regression for no
  benefit while every entry is in-tree.

And the honesty constraint that governs the docs: **"adding a provider is one line" is false
and must not be written.** Per-surface UI, probe quirks and provider behaviour stay manual.
The registry makes omission a *build failure*, not a smaller job.

## Ledger discipline

Every escape-hatch entry carries `reason` + `expires`. The stated tripwire: if the local
exceptions ledger reaches 5 entries, or `intentionally_absent` covers more than 20% of
control descriptors, **the spec is wrong — redesign it, do not add entries.**
