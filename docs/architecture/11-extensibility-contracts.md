# 11 — Extensibility contracts

Status: **as built, 2026-08-09** (Phase 8 of
[../extending/PROGRAM.md](../extending/PROGRAM.md)).

> [PROGRAM.md](../extending/PROGRAM.md) §8 says this document "slots after the
> existing 01-09". That is stale: `10-audit-2026-08-08.md` landed first, so
> this is **11**. Take the next free number, do not trust the spec's.

## The single rule

> A fact is stated **once**, in a frozen spec dataclass in a registry.
> Everything downstream is **derived at runtime**, **generated at build time**
> with `--check` in lint, or **projection-tested**. Anything in none of those
> three categories is a mirror, and the build fails on it.

The ladder, strongest first:

| Rung | Mechanism | Gate |
|---|---|---|
| derive | consumer imports the registry | nothing to break |
| generate | block between markers | `scripts/generate-registry-artifacts.py --check` in `ci.sh lint` |
| projection-test | parse the surface, compare to the registry | a named kit test |
| **mirror** | a second copy | `scripts/check-registry-mirrors.py` fails the build |

## Registry ownership

| Registry | Owner | Projection | Kit |
|---|---|---|---|
| `CLOUD_PROVIDERS` | `netllm-core` (`cloud_providers.py`) | `admin.cloud_provider_registry_payload` → `GET /netllm/v1/cloud/providers` | `tests/conformance/kit_cloud.py` |
| `LOCAL_PROVIDERS` | `netllm-core` (`local_providers.py`) | `admin.local_provider_registry_payload` → `GET /netllm/v1/local-providers` | `tests/conformance/kit_local.py` |
| `SECTIONS` | `netllm-core` (`config_schema.py`) | `config_schema_document()` → `GET /netllm/v1/config/schema` | `tests/conformance/kit_config_surfaces.py` |
| `CONTROLS` | `netllm-core` (`control_plane.py`) | same endpoint, `"controls"` key | `tests/conformance/kit_config_surfaces.py` |
| `SURFACE_SPECS` / `Surface` | `netllm-agent` (`taxonomy.py`) | `tests/contract/routes.json` | none — `tests/contract/test_surface_adapters.py` |
| `KNOWN_HARNESSES` | `netllm-core` (`known_harnesses.py`) | `admin.harness_registry_payload` → `GET /netllm/v1/harnesses` | none |
| `MIGRATIONS` | — | — | **does not exist** (Phase 6, unlanded) |
| `DEPRECATIONS` | — | — | **does not exist** (Phase 6, unlanded) |

`netllm-discovery`, `netllm-cli` and `netllm-mac` own **no** provider
registry. They consume.

## Ledgers, and the tripwires that make them honest

| Ledger | Gates | Today |
|---|---|---|
| `tests/conformance/ledgers/mirrors.toml` | a provider id literal outside its registry | 10 ids, 2 fact classes, 10 files scanned |
| `tests/conformance/ledgers/surface-seams.toml` | a `Surface`-keyed branch outside `surfaces/` | **empty** |
| `tests/conformance/ledgers/control-parity.toml` | a control absent from a required surface | under the tripwire |
| `tests/contract/allowed-divergences.txt` | a behaviour change with no declared reason | **empty** |

Every entry carries a `reason` **and** an `expires`; both are asserted, and
staleness is asserted separately. Stated tripwires:

- `intentionally_absent` covering **>20%** of control descriptors ⇒ the spec
  is wrong. Redesign, do not add entries.
- A local-exceptions ledger reaching **5** entries ⇒ same.
- More than **8** `DROP` entries in a wire `FieldContract` ⇒ same (the
  contract type is Phase G2 and does not exist yet).

## What is deliberately **not** derived

Recorded here because saying no is most of the value, and because each of
these is a place a future reader will otherwise file a bug.

1. **`ProviderId` / `CloudProviderId` / `SurfaceName` stay hand-written
   `Literal`s.** A derived `Literal` blinds basedpyright — no exhaustiveness,
   no completion — and opening them to validated `str` loses pydantic
   parse-time rejection. Asserted by `get_args` equality instead.
   `ProviderId`'s assertion has existed since Phase 3;
   **`CloudProviderId`'s was added in Phase 8**, because `mirrors.toml` had
   claimed since Phase 0 that `kit_cloud` asserted it and `kit_cloud` did not.
2. **No generated SwiftUI or dashboard JS.** ~1100 of `bf67238`'s 1268 lines
   were genuine per-surface UI work. Generate the *manifest of what must
   exist*; hand-write the UI.
3. **`SURFACE_MEMBERS` in `scripts/check-engine-erosion.py` keeps its
   literal** — its no-import-coupling argument is correct. The compensating
   test-side superset assertion that PROGRAM.md specifies **was never
   written**; the literal today contains `RESPONSES`, which is not a `Surface`
   member, which is precisely the drift it was meant to catch.
4. **`_FULL_REPLACE_DICT_PATHS` stays hand-declared.** Full-replace versus
   deep-merge is genuine semantics. Completeness is gated; the answer is not
   synthesized.
5. **No plugin boundary.** No `netllm_ext`, no entry points, no
   `[extensions]` section. The registries are already the right shape to
   expose if a third party ever appears.

## The extension cost, measured

Not estimated — asserted, by `tests/extending/`.

| Axis | Registry entries | Hand-written companions | Generated blocks | Test files you edit |
|---|---|---|---|---|
| A — cloud provider | 1 | **3** | 2 | 0 |
| B — local provider | 1 | **2** | 2 | 0 |
| C — API surface | 1 spec + 1 adapter | — | 1 (`routes.json`) | **1** (`ADAPTERS` tuple) |
| D — control | 1 descriptor | per-surface UI | 1 (obligations table) | 0 |

**"Adding a provider is one line" is false and is not written anywhere in
this document set.** The registry makes omission a *build failure*, not a
smaller job. Per-surface UI, probe quirks and provider behaviour stay manual,
and each guide marks its unguarded rows explicitly.

## The exhaustiveness property

`tests/extending/test_worked_example_local.py` and
`test_worked_example_cloud.py` each inject a fixture registry entry and drive
it end to end. Three properties, not one:

1. **Sufficiency** — entry + declared companions, nothing else, passes every
   stage. A *new* hand-edit becoming necessary fails the stage that needs it.
2. **Necessity** — omitting any companion must break something. A companion
   the machinery now derives fails here, so the list cannot rot into a
   pessimistic over-statement.
3. **Classification** — every mirror `mirrors.toml` allows must be classified
   as a companion, a generated block, or a capability branch. A new ledger row
   fails until the worked example and its guide say which.

Measured red for all three during Phase 8 by: deleting a companion
(→ `ValidationError` naming the `Literal`), replacing a derived constant with
a hand-written map (→ `cli-listing` stage red), adding an unclassified ledger
row (→ classification red), and declaring a companion nothing needs
(→ necessity red).

## Citing a mirror

**Anchor on the identifier, never a line number.**
[PROGRAM.md](../extending/PROGRAM.md) §8 cites the macOS typed-struct mirrors
at `NetllmConfigDocument.swift:28-35,49-88,99-119,122-150`; every range is
stale (the declarations are at 27, 36, 55, 102, 113, 130, 141 today).
`TAB_RENDERERS` is cited at `:2499`, was corrected in Phase 4 to `:2524`, and
is at `:2624` today — the correction went stale too. Every helper in
`tests/conformance/projections.py` searches for a marker and *reports* the
line it found.

## Where the rail is thin

Honest gaps, so nobody has to rediscover them:

- **Axis C has no kit.** Eleven adapter contract tests parameterize over a
  hand-written `ADAPTERS` tuple, so a new surface silently skips them all.
- **Axis F has no kit and a shadow roster.** `connect.py`'s `_guides()` is a
  second hand-written harness roster; Phase 8 added the parity assert that
  stops it from being a hard `KeyError`, but `HarnessSpec` is unlanded.
- **Axis G has no counterpart to diff against** for the Anthropic SDK, and
  `client.py`'s `**payload` splat turns an untyped Messages field into a 502
  after the whole failover budget burns. This fires today.
- **`mirrors.toml` has no `harness-id` or `wire-field` fact class**, both
  scheduled in the Phase-0 addendum and neither landed.
- **`mirrors.toml`'s `current_phase` is `phase-5a`** and cannot advance,
  because 5b and 6 have not landed. Several ledger rows expire at `phase-8`
  and are therefore *not* overdue — the clock is honest, but it is measuring
  refactor phases, not calendar time.

## See also

- [../extending/README.md](../extending/README.md) — the guide index
- [../extending/PROGRAM.md](../extending/PROGRAM.md) — the adopted program
- [../extending/08-control-parity.md](../extending/08-control-parity.md) — Axis D as built
- [../compatibility-policy.md](../compatibility-policy.md) — what versions promise, and what is enforced
