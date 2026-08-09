# Adding a CLI command or a control (Axis D)

Two different jobs share this axis, and conflating them is the mistake the
original Axis D design made:

1. **A new config field** — something an operator can set. The obligation is
   that it is *editable on every surface that ought to carry it*.
2. **A new control** — a tab, a feature, an action (`drain`, `rediscover`,
   `cloud enable`). The obligation is that it *exists* on the dashboard, the
   macOS app and the CLI, or is ledgered with a dated reason.

The design as built — and why one descriptor per config field was measured,
failed [PROGRAM.md](PROGRAM.md)'s own >20% tripwire, and was replaced — is in
[08-control-parity.md](08-control-parity.md). Read that for the *why*; this
file is the *how*.

> **This is not one line, and it is not meant to be.** `bf67238` touched 32
> files and ~1100 of its 1268 lines were genuine per-surface UI work.
> [PROGRAM.md](PROGRAM.md) §6.3 refuses to generate SwiftUI or dashboard JS
> and is right to. What Axis D buys is that **forgetting** a surface is a
> named test failure instead of a bug report six weeks later.

## Job 1 — a new config field

Add the field to its section model in
`packages/netllm-core/src/netllm_core/models.py`, then give it a control on
**both** the dashboard and the macOS app — or a dated row in
`tests/conformance/ledgers/control-parity.toml`.

`tests/conformance/kit_config_surfaces.py` computes a *disposition* for every
field on every surface: schema-rendered, hand-rendered, derived, or ledgered.
Anything else fails by name. There is no fifth option and "we forgot" is not
a reason.

Widget and secrecy hints come from `Field(json_schema_extra={...})` —
`"widget"`, `"write_only"`, `"read_only"`, `"group"`, `"options_from"`,
`"default_factory"`. Grep `models.py` for live examples.

**The destructive case to understand.** `config_merge` rebuilds row types
that have no identity key (`RoutingPolicy`) from the model's defaults plus
whatever the patch sends. A field the Swift struct does not declare is
therefore **erased on Save**, not left alone. That is how
`RoutingPolicy.source` was lost, silently widening a source-scoped policy to
every caller. Identity-keyed rows (`BackendOverride`, `SourceConfig`) are
seeded from the prior dump, so omission there is non-destructive — the field
is merely not editable from that surface, which is a parity question rather
than data loss. The kit asserts the two cases differently on purpose.

## Job 2 — a new control

Add a `ControlDescriptor` to `CONTROLS` in
`packages/netllm-core/src/netllm_core/control_plane.py`. Granularity is a
**tab or feature**, not a config field.

| Field | Means |
|---|---|
| `key` | stable id; for tabs, also the dashboard tab key |
| `kind` | `config` / `view` / `action` |
| `dashboard_renderer` | identifier that must appear as a `TAB_RENDERERS` value |
| `swift_symbol` | identifier or `sectionHeader("…")` call in the macOS settings source |
| `surfaces_required` | genuinely per-control — `ui.menubar_*` is a macOS concept, `netllm install` is CLI-only |
| `admin_route` | discriminating for `action`; left empty for `config` (all fields go through one route, so asserting it would be a tautology) |
| `cli` | leaf command paths as Typer renders them |
| `is_tab` | `False` for actions living inside another tab |

Then write the UI on each required surface by hand, and add the Typer command
in `packages/netllm-cli/src/netllm_cli/commands/` (`main.py` is wiring only).

The descriptor **never generates UI**. It generates the *table of
obligations* in [08-control-parity.md](08-control-parity.md), between
markers, via `python3 scripts/generate-registry-artifacts.py`.

## Checklist

Rows marked ***unguarded*** have no test behind them.

| # | Step | Guard (`tests/conformance/kit_config_surfaces.py::…` unless stated) |
|---|---|---|
| 1 | New config field has a disposition on every surface | `test_every_config_field_has_a_disposition_on_every_surface` |
| 2 | Read-only fields are derived, not editable, on every surface | `test_read_only_fields_are_derived_on_every_surface` |
| 3 | Identityless Swift struct carries every model field | `test_identityless_swift_struct_carries_every_model_field` |
| 4 | Identity-keyed omission proven non-destructive | `test_identity_keyed_omission_is_non_destructive` |
| 5 | `RoutingPolicy.source` survives a Swift-shaped save | `test_routing_policy_source_survives_a_swift_shaped_save` |
| 6 | Field round-trips through config import/export | `test_config_import_export_round_trips_every_field` |
| 7 | Every model field has a schema entry | `tests/test_config_schema.py::test_every_pydantic_field_has_a_schema_entry` |
| 8 | Secrets are write-only in the schema | `tests/test_config_schema.py::test_secrets_are_write_only` |
| 9 | Descriptor's tab renderer exists in `dashboard.js` **and** `index.html` | `test_control_exists_on_the_dashboard` |
| 10 | Descriptor's `swift_symbol` exists in the macOS settings source | `test_control_exists_on_macos` |
| 11 | `action` controls name a CLI command Typer really registers | `test_action_controls_have_a_real_cli_command` |
| 12 | Declared `admin_route` exists in the route manifest | `test_declared_admin_route_exists` |
| 13 | Descriptors served additively on `GET /netllm/v1/config/schema` | `test_descriptors_are_served_additively_on_the_schema_endpoint` |
| 14 | The generic schema machinery still exists on both surfaces | `test_the_generic_schema_machinery_exists_on_both_surfaces` |
| 15 | Any exception is ledgered with a real reason **and** a date | `test_every_control_ledger_entry_has_a_real_reason_and_expiry`, `test_every_ledger_entry_has_a_real_reason_and_expiry` |
| 16 | No ledger entry is overdue | `test_no_control_ledger_entry_is_overdue`, `test_no_ledger_entry_is_overdue` |
| 17 | Ledger has not tripped the >20% tripwire | `test_the_control_parity_ledger_is_under_the_tripwire`, `test_the_ledger_tripwire_is_not_yet_tripped` |
| 18 | No ledger row excuses a field that is in fact rendered | `test_no_ledger_entry_excuses_a_field_that_is_actually_rendered` |
| 19 | Obligations table regenerated | `python3 scripts/generate-registry-artifacts.py --check` (in `ci.sh lint`) |
| — | **The widget is the right widget** and the control is usable | **unguarded** — presence is asserted, never quality. [PROGRAM.md](PROGRAM.md) §6.3 |
| — | **The macOS app is rebuilt and the symbol actually renders** | **unguarded from Python** — the guard parses Swift *source text*; it cannot run the app |
| — | **CLI output formatting** | **unguarded** — Typer introspection proves the command exists, not that its table reads well |
| — | **`dashboard.js` (2826 lines) / `SettingsWindowView.swift` (1237 lines) size** | **acknowledged debt, not scheduled** — [PROGRAM.md](PROGRAM.md) §6.6. The parity gate makes their *gaps* loud without touching their *size* |

## Run it

```bash
uv run pytest tests/conformance/kit_config_surfaces.py -k <your-control-key>
```

For a config field, the selector is the field name:

```bash
uv run pytest tests/conformance/kit_config_surfaces.py -k <your_field_name>
```
