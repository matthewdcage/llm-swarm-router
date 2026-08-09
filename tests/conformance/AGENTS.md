# tests/conformance

Parent: [../AGENTS.md](../AGENTS.md).

## Purpose

Registry-parameterized **conformance kits** and their **ledgers**. A kit is
the executable contract for one extension axis: it parameterizes over a
registry, so a new entry acquires the whole suite with **zero test-file
edits**. That property is the reason the registries are worth having.

## Ownership

| File | Contract |
|---|---|
| `kit_cloud.py` | Axis A — every `CLOUD_PROVIDERS` entry |
| `kit_local.py` | Axis B — every `LOCAL_PROVIDERS` entry |
| `kit_config_surfaces.py` | Axis D — every config field's disposition on every editing surface, and every `ControlDescriptor`'s presence obligations |
| `projections.py` | read a fact back out of Swift / JS / TOML / Markdown, returning `(values, source_location)` |
| `ledgers/mirrors.toml` | files allowed to restate a provider id |
| `ledgers/surface-seams.toml` | `Surface`-keyed branches allowed outside `surfaces/` (**empty**) |
| `ledgers/control-parity.toml` | controls and row fields allowed to be absent from a surface |

## Local Contracts

- **`kit_*.py` is collected**: `pyproject.toml`'s `python_files` includes
  `kit_*.py`. A file named anything else is silently uncollected, which is
  worse than not writing it.
- **A kit must be green with zero entries wired** before any entry is added.
  Every gate in this program ships ledger-seeded green on day one.
- **Failures must name a location.** `projections.py` returns
  `SettingsViewModel.swift:94 is missing 'dashscope'`, not
  `assert set1 == set2`. The location is what makes a red test actionable by
  someone who has never seen the file.
- **Anchor projections on identifiers, never line numbers.** Search for the
  marker, *report* the line. Every line number written into a spec in this
  repo has gone stale, including the corrections.
- **A text scan must not count prose.** A comment naming a control is not
  that control; `strip_comments` and `SourceRegion.has_symbol` exist because
  this repo shipped that mistake twice, and a guard that counts prose fires
  green on its own changelog.
- **Every ledger row carries `reason` **and** `expires`**, both asserted, plus
  a staleness test. A reason with no date is a decision nobody revisits.
- **Adding a ledger row is not a fix.** It also turns
  `tests/extending/test_worked_example_*.py::test_the_companion_list_is_exhaustive`
  red until the worked example classifies the new mirror.
- **Tripwires**: `intentionally_absent` over **20%** of control descriptors,
  or a local-exceptions ledger reaching **5** entries, means the spec is
  wrong — redesign, do not add entries.

## Extension contract

- **Owns:** no registry. Kits are *consumers* — the facts live in
  `netllm-core` and `netllm-agent`.
- **Serves:** the executable contract each `docs/extending/` guide's
  checklist points at, one row per named test.
- **No new mirrors:** a kit may not hard-code a roster. If a kit needs a list
  of ids, it takes it from the registry or the test is a mirror of the thing
  it exists to protect (`PROTOCOL_MEMBERS` was exactly that mistake).

## Verification

```bash
uv run pytest tests/conformance -q
uv run pytest tests/conformance/kit_local.py -k <provider-id>
python3 scripts/check-registry-mirrors.py
python3 scripts/check-engine-erosion.py --seams
```

## Child DOX Index

None — flat directory plus `ledgers/`.
