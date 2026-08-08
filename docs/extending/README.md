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
| **B** local backend | ~14 (no example exists) | **11 parallel maps** on the same id | **zero tests** reference `KNOWN_PROVIDERS` | **Worst axis** |
| **C** API surface | 1–3 for a dialect bridge | 4 escaped `Surface` branches | real AST gate in CI | **Cleanest by a wide margin** |
| **D** CLI + control plane | 32 (measured, `bf67238`) | 3-surface parity manual | per-surface tests; **no parity test** | High volume, mostly genuine work |
| **E** config evolution | 1–6 | 2 hand-maintained allowlists | strong schema tests; **no version, no migration** | Structurally missing |

Axis C scoring cleanest is the consolidation paying off — the `SurfaceAdapter` seam does
absorb new work. Axis B is the inverse: eleven maps keyed on the same provider id, and not
one test referencing the roster.

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
- **No rewrite of `dashboard.js` (2826 lines) or `SettingsWindowView.swift` (1237).**
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
