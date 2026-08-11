# Axis D — cross-surface control parity

> A control exists on every surface that owes it one, or the absence is written
> down with a reason and a date.

This is [PROGRAM.md](PROGRAM.md) §3 Axis D and closes
[F-21](../architecture/07-findings-register.md) ("the config schema mirror is
reduced, not eliminated"). It is **not** the literal Axis D text — the literal
shape was measured and rejected. That decision, and the evidence for it, is
§1 below. Read it before adding anything here.

Nothing in this axis generates UI. PROGRAM.md §6.3 refuses to generate SwiftUI
or dashboard JS from a descriptor and is right: ~1100 of `bf67238`'s 1268 lines
were genuine per-surface UI work. What is generated is the *manifest of what
must exist* — the table in §5, and nothing else.

---

## 1. The deliberate deviation, and the measurement behind it

PROGRAM.md §3 Axis D specifies one `ControlDescriptor` per config field, with
`cli`, `dashboard_renderer`, `swift_symbol`, `admin_route`,
`surfaces_required` and `intentionally_absent`.

PROGRAM.md §7 also states the tripwire that governs every ledger in this
program:

> if `local-exceptions.toml` reaches 5 entries, or `intentionally_absent`
> covers **>20%** of control descriptors, the spec is wrong — redesign it, do
> not add entries.

A reconnaissance pass built the literal shape and measured it against that
tripwire. It fails:

| Shape measured | `intentionally_absent` coverage | Verdict |
| --- | --- | --- |
| one descriptor per config field, CLI excluded from `surfaces_required` | **22.2%** | over the tripwire |
| one descriptor per config field, CLI required | **62%** | far over |

It also fails the program's own purpose. One descriptor per field adds roughly
500 hand-maintained facts and deletes none: a fifth mirror wearing a registry's
clothes, next to `dashboard.js`, `SettingsWindowView.swift`,
`NetllmConfigDocument.swift` and `config.example.toml`. PROGRAM.md §1's rule is
that a fact is stated **once**; a per-field descriptor restates every field's
existence a sixth time in order to check the other five.

So the shape was redesigned, exactly as the tripwire instructs:

| Concern | Literal spec | Built instead |
| --- | --- | --- |
| Unit of control | one config field | one **tab / feature** — a thing with a renderer, a button, a Swift symbol and often a CLI command family |
| Field-level parity | a descriptor per field | a **coverage disposition** computed from the surfaces themselves (§3), needing no registry at all |
| CLI | `cli` required per field | required for **action** controls only; config parity is asserted as an import/export round-trip (§4) |
| `intentionally_absent` | a dict on the descriptor | `tests/conformance/ledgers/control-parity.toml`, under the ledger-discipline tests that already exist |

Day-one measurements of what was actually built are in §6.

## 2. `ControlDescriptor` — tab/feature granularity

`packages/netllm-core/src/netllm_core/control_plane.py`. Served **additively**
on the existing `GET /netllm/v1/config/schema` under `"controls"` — no new
endpoint, and a client that has never heard of the key ignores it.

Each descriptor names a *presence unit per surface*, which is the point:

- `sources` is a **section of the Integrations page** on the web
  (`renderSourcesSection` in `static/pages/integrations.js`) and a **section
  inside `routingTab`** on macOS (`sectionHeader("Sources")`). A naive tab-set
  diff calls that absent on both; a human calls it present, and the human is
  right. `swift_symbol` carries the section header, so it passes.
- The presence unit is a *section*, not necessarily a whole page. When the
  dashboard collapsed fourteen tabs into eleven pages, `agent`, `discovery`
  and `swarm` all landed on the Network page: each descriptor names its own
  section renderer (`networkThisNodeSection`, `networkLocalProvidersSection`,
  `networkSwarmDiscoverySection`), so deleting one section still fails even
  though the page survives. Which page carries which control is declared in
  `DASHBOARD_CONTROLS` in `tests/conformance/kit_config_surfaces.py`.
- `drain` and `rediscover` are not pages at all (`is_tab=False`); they are
  buttons in the persistent chrome and a CLI command each.
- `surfaces_required` is genuinely per-control. `ui.menubar_*` is a macOS
  concept; `netllm install` / `connect` / `env` / `swarm-token` are CLI-only by
  nature. A uniform roster would be a lie.

`admin_route` is only asserted where it discriminates. All 99 config schema
keys go through `POST /netllm/v1/admin/config`, so asserting that route for a
config control is a tautology; the field is left empty for those.

## 3. Field parity is a disposition, not a descriptor

`tests/conformance/kit_config_surfaces.py` — the same file that already carried
`INTENTIONALLY_ABSENT`, reason-quality assertions, stale-excuse detection,
`PHASE_ORDER`, expiry validation and an executable tripwire. This is F-21's own
prescribed fix, and it needs no new registry:

> add a drift test that asserts every `NetllmConfig` field is either
> schema-rendered or explicitly listed in a `KNOWN_UNRENDERED` allowlist — so
> adding a Python field forces a conscious client decision.

Every schema key resolves to exactly one of four answers, **per surface**:

| Disposition | Meaning | Evidence asserted |
| --- | --- | --- |
| `derived` | `read_only`; dropped by both generic renderers by construction | `schemaFieldsCard`'s `!f.read_only` filter, `SchemaFormView`'s `readOnly` filter |
| `schema_rendered` | covered by that surface's generic schema form | the renderer call itself (`renderSchemaForm("ui"`, `SchemaFormView(`) |
| `hand_rendered` | named in the hand-written renderer for that subtree | the field name as a quoted key or a property access, in comment-stripped source |
| `ledgered` | declared absent, with a reason and an expiry | `ledgers/control-parity.toml` |

Three properties of this that are easy to get wrong, and were:

**`schema_rendered` has to be first-class.** `SettingsWindowView.swift` renders
all ten `ui.*` fields through one `SchemaFormView(fields: uiFields, …)` and the
names never appear in Swift source at all. `renderSchemaForm("swarm", …)`
covers nine dashboard fields with zero name literals. Without this
disposition, every schema-driven field is a false positive.

**`derived` is excluded from the denominator.** Both generic renderers drop
`read_only` fields, so every one of them is absent on every surface, correctly
and forever. Counting them would open with a permanent ledger row each and a
distorted percentage.

**`derived` is about *rendering*, not about the wire.** `row_id` on a
`routing.backends`/`routing.sources` row is `read_only` — no surface renders a
control for it — but it is also flagged `identity`, and both patch builders
must send it straight back so the agent can tell which stored row an edit
belongs to. Treating "read_only" as "drop it from the patch" is exactly what
made editing a backend's `base_url` erase its stored API key. Absent from the
form is not the same as absent from the payload; see
`tests/test_config_row_identity.py`.

**A name in prose is not a control.** `source_region` strips `//`, `///` and
`/* */` comments before scanning, and `_names` requires the field name as
`"name"` or `.name` with a word boundary. Both matter here: `dashboard.js`
carries the help string "an above-default max_concurrency", and
`backendOverrideEditor` binds `.api_key_env` — which a substring scan reads as
carrying `api_key`.

A ledger entry covers its key **and everything nested beneath it**, so one row
answers for `routing.sources[].scenarios` and its five child fields. Rows are
still counted per key when the percentage is measured.

## 4. The CLI, asserted as what it actually is

`cli` is dropped from `surfaces_required` for config-kind controls. Only 2 of
50 config field names appear as Typer options and both are runtime flags;
demanding field-level CLI parity would mandate ~30 options nobody asked for.

What is asserted instead:

1. **`netllm config export | netllm config import` round-trips every field** —
   the CLI's real config obligation, run through the actual commands with a
   populated config.
2. **Every action-kind control has a real command**, and every command any
   descriptor names resolves, by real Typer introspection.

The introspection has a trap worth stating once. With typer 0.26 / click 8.4,
`typer.main.get_command(app)` returns a `typer.core.TyperGroup`, which is
**not** a `click.Group` subclass — `isinstance(cmd, click.Group)` is `False`
and silently collapses the whole tree to one leaf, after which every CLI
assertion passes vacuously. The walk duck-types on `.commands`, and
`test_the_cli_walk_does_not_collapse` is the regression guard.

## 5. The controls

<!-- netllm:generated:begin:control-parity-table -->
| Control | Kind | Dashboard | macOS | CLI |
| --- | --- | --- | --- | --- |
| `status` | view | `renderOverviewPage` | `homeTab` | `netllm status` |
| `serving` | view | `ovRenderThroughput` | `homeTab` | n/a |
| `backends` | view | `renderBackendsPage` | `backendsTab` | n/a |
| `models` | view | `renderModelsPage` | `modelsTab` | `netllm models` |
| `peers` | view | `renderPeersPage` | `peersTab` | `netllm peers` |
| `agent` | config | `networkThisNodeSection` | `networkTab` | n/a |
| `discovery` | config | `networkLocalProvidersSection` | `networkTab` | `netllm discover`, `netllm ignore list`, `netllm ignore add`, `netllm ignore remove` |
| `swarm` | config | `networkSwarmDiscoverySection` | `networkTab` | `netllm join`, `netllm swarm-token` |
| `routing` | config | `renderRoutingPage` | `routingTab` | n/a |
| `sources` | config | `renderSourcesSection` | `integrationsTab` | `netllm sources list`, `netllm sources toggle` |
| `cloud` | config | `renderCloudPage` | `cloudTab` | `netllm cloud list`, `netllm cloud enable`, `netllm cloud disable`, `netllm cloud set-key`, `netllm cloud verify` |
| `cloud_verify` | action | `renderCloudVerificationRow` | `verifyCloudProvider` | `netllm cloud verify` |
| `ui` | config | `prefsBehaviourSection` | `preferencesTab` | n/a |
| `logs` | view | `renderLogsPage` | `logsTab` | n/a |
| `tools` | action | `renderDoctorPage` | `toolsTab` | `netllm doctor`, `netllm test`, `netllm gateway` |
| `drain` | action | `renderDrainButton` | `drainButton` | `netllm drain` |
| `rediscover` | action | `runDiscover` | `runDiscover` | `netllm discover` |
<!-- netllm:generated:end:control-parity-table -->

Regenerate with `python3 scripts/generate-registry-artifacts.py`; `--check`
runs in `scripts/ci.sh lint`.

## 6. Day-one numbers, and the denominators they are measured against

A percentage without its denominator is decoration, so both are stated.

**Control descriptors:** 2 of 16 absent = **12.5%**. `serving` and `drain`,
both on macOS, both with an expiry. Under the 20% tripwire.

**Field parity:** the denominator is **(schema keys − derived) × editing
surfaces** = (99 − 5) × 2 = **188 (field, surface) pairs**. The editing
surfaces are the dashboard and the macOS app; the CLI is not a per-field
surface (§4). Ledgered: **18 pairs = 9.6%**, from 12 ledger rows. The
remainder: 87 `schema_rendered`, 83 `hand_rendered`.

Both are asserted, not asserted-about:
`test_the_control_parity_ledger_is_under_the_tripwire` fails at >20%.

## 7. What the day-one gaps were, and what was done with each

Six candidate gaps were examined. Four were closed, three were judged correctly
absent and ledgered. Closing them **first** is why the gate is green by fixing
things rather than by writing excuses — the gate was not designed around the
gaps.

### Closed

| Field | Was | Now |
| --- | --- | --- |
| `routing.upstream_connect_timeout_s`, `routing.upstream_read_timeout_s` | promoted to config in `bb3eae0` for F-22 and given a control on **no** surface; not even exported by `config_summary`, so the 120 s read timeout stayed effectively source-only — the case F-22 called out as most likely to bite | exported by `config_summary`; number fields on the dashboard's Routing tab and in `routingTab` on macOS |
| `agent.max_concurrency` | macOS only | rendered on the dashboard's Agent tab from the schema |
| `routing.model_aliases` | macOS had a full `modelAliasEditor`; `dashboard.js` mentioned it only in a comment | rendered on the Routing tab. `schemaDictListStringsRow` grew an add-key/remove-key affordance — without it the widget could only edit aliases that already existed in `config.toml`, which is a control that is present but unusable |
| `cloud.providers[].api_key_env`, `cloud.providers[].base_url` | absent on both UIs, though the agent honours both (`service/cloud.py` resolves `api_key_env` ahead of the registry env var and `base_url` ahead of the registry endpoint) and both are already generically rendered for `routing.backends` rows | rendered on both. `_cloud_provider_export` now sends them too — a control bound to a value the summary never sends reads as empty and POSTs `""` back |

### Judged correctly absent

**`routing.require_same_model_for_shard`** — *not* exposed, on either surface.
It is deprecated and inert: its only consumer, `plan_batch_shard`, was deleted
as dead code, the field is kept solely so existing `config.toml` files still
load, and it was deliberately removed from `config_summary` and both UIs in
0.4.6 under audit **F-17** ("fields exist in clients that Python has
deprecated"). Adding a toggle that does nothing would re-open the finding.
Ledgered on both surfaces, expiring with the field itself.

**`cloud.providers[].auth`** — CLI-owned, not a form field. Its valid values
are per-provider (`CloudProviderSpec.auth_modes`), which the shape-only schema
does not carry, and two of the three modes cannot be completed from a GUI at
all: `oauth_pkce` needs the browser and local callback server that `netllm
cloud connect` runs, `plan_token` needs `claude setup-token` in a terminal. A
picker would let a user select a mode neither UI can finish. Ledgered `never`
on both surfaces, with the reason.

**`cloud.providers[].api_key` on macOS** — the app stores cloud keys in the
login Keychain and injects them into the agent process; it deliberately never
writes the key into `config.toml`. The *control* exists (a `SecureField` with
Save key / Clear key); what is absent is the config field, and that absence is
the security property. Ledgered `never`.

The remaining macOS entries are honest gaps with expiries:
`routing.policies[].source`, three `routing.backends[]` fields, and the three
`routing.sources[]` collection fields `SchemaFormView` has no widget for.

## 8. What this axis still cannot assert

Stated because R3 ("projections prove presence, not correctness") applies here
in its sharpest form.

- **That a rendered control works.** These are presence obligations. A field
  can be rendered against the wrong draft object, bound to the wrong path, or
  saved into the wrong patch, and every disposition here stays green. The
  round-trip tests in this file and `tests/test_dashboard_ui_wiring.py` cover
  the specific defects that have actually occurred; nothing covers the class.
- **That a `schema_rendered` field is on screen.** The evidence is the renderer
  call, not the widget. If `SchemaFormView` silently dropped a widget kind,
  every field it covers would still read as present. This is the price of the
  disposition existing at all, and it is cheaper than the alternative (which
  is calling all 87 generically-rendered (field, surface) pairs absent).
- **Anything about a third surface.** The CLI is asserted as a round-trip and
  an action roster, not field by field, on purpose (§4).
- **Region boundaries beyond the anchors.** Regions are delimited by
  identifiers, never line numbers — PROGRAM.md cites `TAB_RENDERERS` at
  `:2499` and the real line is `:2524`, which is exactly why. Moving a renderer
  without moving its anchor changes what a region covers, and the failure would
  be a false *positive*, not a false negative.

## 9. Running it

```bash
uv run pytest tests/conformance/kit_config_surfaces.py -q
# the ledger's own health -- reasons, expiries, staleness, the tripwire
uv run pytest tests/conformance/kit_config_surfaces.py -k "ledger or parity"
python3 scripts/generate-registry-artifacts.py --check
```

The second selector is the ledger half: `-k control-parity` selected **nothing**
(176 deselected, a vacuous pass) because no test id contains a hyphen —
`control-parity` is the *ledger file's* name, not a test name. `"ledger or
parity"` selects the seven that read it.

Adding a config field with no control fails as:

```
routing.new_knob has no control on the dashboard surface: dashboard.js:1945
does not name 'new_knob'. Render it, or declare it in
tests/conformance/ledgers/control-parity.toml with a reason and an expiry.
```
