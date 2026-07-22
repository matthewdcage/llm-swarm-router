# Config schema rewrite plan — eliminate the Python/Swift/JS triple-mirror

Status: **in progress** — phase 1 done (2026-07-22): `netllm_core/config_schema.py`
+ `GET /netllm/v1/config/schema`, with `json_schema_extra` widget/secrecy/
read-only hints on the 6 editable sections and a drift-regression test
(`tests/test_config_schema.py`). Phases 2–5 (JS renderer, Swift dynamic
model, docs cleanup) not started. Companion to
[cloud-providers-plan.md](cloud-providers-plan.md) §"Schema triple-mirror
drift" and the earlier `routing-hardening-plan.md` follow-up of the same
name. This is the deferred, larger half of that item: a generic schema for
the **editable** config shape (`agent`, `discovery`, `swarm`, `routing`,
`ui`, `cloud`) so the macOS app and web dashboard render forms from data
instead of hand-authored structs/functions. Scope, phases, and the
non-breaking contract only — no implementation here.

## 1. Problem

Six config sections are hand-authored **three times** today:

| Layer | File | What it duplicates |
|---|---|---|
| Source of truth | `packages/netllm-core/src/netllm_core/models.py` (12 pydantic models) | Field names, types, defaults, validators |
| macOS | `apps/netllm-mac/Sources/Config/NetllmConfigDocument.swift` (7 `Codable` structs) | Same shape, by hand, in Swift |
| Web dashboard | `packages/netllm-agent/src/netllm_agent/static/dashboard.js` (`emptyConfigDraft()` + 12 `render*Tab()` functions, ~700 lines) | Same shape *and* the entire form UI, by hand, in JS |

Consequence: adding, renaming, or retyping a field is a 3-file (sometimes
4, with `admin.py`'s `config_summary`/`apply_config_patch`) manual edit.
Miss one and the symptom is silent — deep-merge means an unmodeled field
neither breaks nor surfaces; it's just uneditable from that surface until
someone notices. The cloud-providers work (2026-07-22) hit exactly this:
`CloudSection`/`CloudProviderConfig` had to be added by hand in Swift, and
a matching block added by hand in `dashboard.js`'s `emptyConfigDraft()` +
a ~150-line `renderCloudTab()`/`renderCloudProviderCard()` pair. That
follow-up also closed the *narrower* metadata-drift case (provider
display text now comes from `GET /netllm/v1/cloud/providers`, not a
hardcoded Swift/JS list) — this plan is the harder, generic case: the
editable config shape itself.

## 2. Goal

A single schema, generated from the pydantic models, that:

1. Both clients fetch at runtime (or a build step, see §5) instead of
   maintaining their own struct/object definitions.
2. Drives generic form rendering (text field, number, toggle, select,
   list-of-strings, list-of-objects) for both Swift and JS, so a new
   pydantic field appears in both UIs with **zero** client-side code.
3. Preserves every non-breaking guarantee the config system already has:
   deep-merge on write, additive-field tolerance on read, TOML round-trip,
   0600 file permissions, write-only secrets (`api_key`, `cluster_token`).
4. Does not require the schema to be perfect on day one — hand-authored
   custom widgets stay possible for fields that need one (see §6).

## 3. Design

### 3.1 Schema source and shape

Pydantic v2 already generates JSON Schema for free: `NetllmConfig.model_json_schema()`.
That's the starting point, but raw JSON Schema is missing everything a
form renderer needs:

- **Field order** (JSON Schema dicts don't guarantee it across languages)
- **Widget hints** (`api_key` should render as a secret input; `models`
  arrays should render as list editors; `region`/`api_format` should
  render as selects with the *registry's* live options, not a static enum)
- **Secrecy** (`api_key`, `cluster_token` must never round-trip to a GET
  response — today enforced ad hoc per-field in `admin.py`)
- **Which fields are user-editable vs. server-computed** (`agent_id`,
  `hostname`, `lan_defaults_applied`, `backend_count` are read/display
  only, not form fields)

Proposal: a thin annotation layer over pydantic's schema, using `Field(
json_schema_extra={...})` (already supported, zero new dependency) to
carry:

```python
region: str = Field(
    default="",
    json_schema_extra={
        "widget": "select",
        "options_from": "registry.regions",  # resolved server-side, per provider
        "group": "Providers",
    },
)
api_key: str = Field(
    default="",
    json_schema_extra={"widget": "secret", "write_only": True},
)
```

A new `netllm_core/config_schema.py` module walks `NetllmConfig` and
emits one document: `{"sections": {"agent": {...}, "cloud": {...}, ...},
"version": "<schema-version>"}`. Each section is a list of field specs:
`{name, type, widget, default, options?, write_only?, group?, help?}`.
Nested/list-of-object fields (`routing.policies`, `routing.backends`,
`cloud.providers`) get a `"widget": "list"` with an `"item_schema"`
recursing into the same structure — this is the part that replaces
`renderRoutingPoliciesEditor`/`renderBackendOverridesEditor`/
`renderCloudProviderCard`'s hand-rolled row-add/remove/sync JS.

### 3.2 Serving it

`GET /netllm/v1/config/schema` (admin-gated, same as every other
`/netllm/v1/*` route) returns the document from §3.1. Cache-friendly:
the schema only changes on a netllm version bump, so both clients can
`ETag`/version-gate it (schema `version` field ties to `get_version()`)
and skip refetching every session.

This is additive to, not a replacement for, `GET /netllm/v1/config`
(`config_summary`) — that endpoint keeps returning **values**; the new
one returns **shape**. Both are needed: a generic form renderer needs
the shape to build inputs and the values endpoint to populate them.

### 3.3 JS: a generic form renderer

New `renderSchemaForm(sectionKey, schema, draft, onDirty)` in
`dashboard.js` (or a new `schema-form.js` if it grows large) replaces
the 12 hand-written `render*Tab()` bodies with one function that:

- Iterates `schema.sections[sectionKey].fields`
- Dispatches on `widget` to the existing DOM helpers (`checkboxRow`,
  `el`/`textEl`, `<select>` builders) already in `dashboard.js` — this
  reuses, not replaces, the low-level widget code from the cloud-tab work
- For `"widget": "list"`, recurses using the row-add/remove pattern
  already proven in `renderRoutingPoliciesEditor`/`renderCloudProviderCard`,
  generalized to take an `item_schema` instead of a hardcoded field list

`emptyConfigDraft()` (currently ~10 hardcoded lines per section) becomes
a single function that walks the fetched schema and produces `{default
value per field}` — the offline/unreachable fallback shrinks from "one
default object per section, hand-maintained" to "walk the last-cached
schema, or a minimal built-in bootstrap for agent/discovery only" (the
sections needed to even reach the agent).

Tabs that need bespoke behavior beyond generic fields (Status, Backends,
Models, Peers, Logs, Doctor — all read-only/live-data views, not config
editors) are **out of scope** for this rewrite; only the 6 editable
config sections move to the schema-driven renderer.

### 3.4 Swift: schema-driven `Codable` at runtime

This is the harder half — Swift wants static types, and a JSON-Schema
document is inherently dynamic. Two viable approaches, pick one at
implementation time based on team preference:

**Option A — dynamic dictionary model.** `NetllmConfigDocument` per
section becomes `[String: JSONValue]` (a small custom `Codable` enum
wrapping string/number/bool/array/object, ~40 lines, no new dependency)
instead of a typed struct. `CloudSettingsView`-style views become one
generic `SchemaFormView(section: String, schema: ConfigSchema, draft:
Binding<[String: JSONValue]>)` that switches on each field's `widget`
the same way the JS renderer does. Loses compile-time field-name safety
for config *editing* (not for the rest of the app — `AgentStatusPayload`,
`BackendStatus`, etc. stay typed, they're not part of this rewrite).

**Option B — build-time codegen.** A small script (Python, run in CI or
a pre-build phase) fetches/reads the schema and emits
`NetllmConfigDocument.generated.swift` — keeps static typing, but adds a
build step and a "regenerate and commit" discipline (similar to
`scripts/generate-dashboard-tokens.py`'s existing pattern for
`dashboard-tokens.css`, which the codebase already has precedent for).

Recommendation: start with **Option A** for the editable-config path
only. It's less total new code, no build-step discipline to maintain,
and the loss of compile-time field safety is contained to config forms
— a category where the whole point is "the shape isn't known at Swift
compile time." Revisit Option B only if Option A's dynamic-dictionary
ergonomics prove painful in practice.

### 3.5 What stays hand-written (by design, not oversight)

- **Cloud provider display metadata** — already solved via `GET
  /netllm/v1/cloud/providers` (2026-07-22); the schema endpoint covers
  *shape*, the registry endpoint covers *cloud-specific content*. No
  overlap to resolve.
- **Non-config views** (Status/Backends/Models/Peers/Logs/Doctor) — live
  data, not editable config; not part of the triple-mirror problem.
- **Secrets flow** (Keychain on macOS, env/`api_key_env` everywhere) —
  the schema marks fields `write_only`, but *how* a write-only field is
  sourced (env var vs. Keychain vs. inline) stays a per-platform decision,
  same as today.
- **Validation error UX** — pydantic validators (e.g. `agent.listen`
  format) produce Python exceptions on `apply_config_patch`; surfacing
  those as inline form errors instead of a save-time toast is a UX
  improvement worth doing here, but is additive, not required for v1.

## 4. Non-breaking contract

Every existing guarantee must survive unchanged:

- `netllm config export`/`import` (Swift's `ConfigStore`, CLI) keep
  working on the **pydantic model**, not the schema — the schema is a
  new, parallel read path for UI generation, not a replacement for the
  existing TOML/JSON round-trip.
- `admin.apply_config_patch`'s deep-merge and preserve-on-omit-secret
  behavior are unchanged; the schema-driven forms just need to *produce*
  patches in the same shape they do today (a generic form serializer
  emitting `{section: {field: value}}` is a strict subset of what the
  hand-written `buildConfigPatch()` already emits).
- Old app/dashboard builds that predate this rewrite keep working against
  a newer agent (they never call `/netllm/v1/config/schema`, so it's
  purely additive from their point of view) — and a newer app/dashboard
  against an older agent (pre-schema-endpoint) must degrade to a fixed
  minimal bootstrap section list (agent/discovery only — enough to reach
  a running agent) rather than a blank Settings window/dashboard.
- No field renames or type changes as part of this rewrite — it changes
  *how* the shape is communicated, not the shape itself. Any actual field
  changes happen in separate, ordinary PRs against `models.py`, same as
  today.

## 5. Phases

Each phase independently shippable and gated on its own tests, mirroring
the cloud-providers rollout discipline.

1. **Schema module + endpoint** (`netllm-core` + `netllm-agent`): 
   `config_schema.py` walking all 6 sections, `GET
   /netllm/v1/config/schema`. No client changes yet — pure addition,
   tested by asserting the emitted shape matches `NetllmConfig`'s actual
   fields (a regression test that fails loudly if a model field is added
   without a schema annotation, closing the "silent drift" failure mode
   from §1 at the source).
2. **JS generic renderer, one section first** (pick `ui` — smallest,
   lowest risk): `renderSchemaForm()` + schema-driven `emptyConfigDraft()`
   for that one section, old hand-written tabs untouched for the other 5.
   Prove the pattern in production before generalizing.
3. **JS: remaining 5 sections**, including the two list-of-object cases
   (`routing.policies`/`routing.backends`) and `cloud` (already has a
   working hand-written implementation to diff against for parity).
   Delete the superseded `render*Tab()` functions once parity is
   confirmed.
4. **Swift: Option A dynamic model**, `ui` section first (same reasoning
   as phase 2), then the remaining 5. `NetllmConfigDocument`'s typed
   structs are deleted section-by-section as each is proven, not in one
   big-bang cutover.
5. **Docs + cleanup**: update `docs/cloud-providers-plan.md` and both
   `AGENTS.md` files that currently describe the hand-mirrored contract;
   remove `CLOUD_PROVIDER_IDS_BOOTSTRAP`-style hardcoded fallbacks in
   favor of the schema's own minimal-bootstrap section (§4).

## 6. Risks / open questions

1. **Form UX regressions.** Hand-written tabs today have section-specific
   affordances (e.g. Routing's "Add routing policy" button pre-fills a
   sensible default policy, not an empty one; Discovery's provider-URL
   editor groups by provider). A generic renderer needs an escape hatch —
   `json_schema_extra` can carry a `"default_factory": "local_openai_policy"`
   hint resolved to a named JS/Swift builder function, but this needs a
   real design pass in phase 2, not an afterthought.
2. **Swift dynamic-dictionary ergonomics.** Option A trades compile-time
   safety for genericity; if `SchemaFormView` proves unwieldy in SwiftUI
   (binding a `[String: JSONValue]` subscript is more ceremony than a
   struct property), fall back to Option B mid-rewrite — the schema
   endpoint (phase 1) is useful either way.
3. **Validation error surfacing.** pydantic raises on `apply_config_patch`
   today (caught, turned into an HTTP 4xx); a generic form has no
   per-field place to show "agent.listen must be host:port" without new
   protocol (the schema could carry validator regex/messages, but pydantic
   `@field_validator` functions aren't introspectable into JSON Schema
   automatically — worth scoping down to regex-expressible validators
   only, falling back to the existing save-time toast for the rest).
4. **Effort vs. payback.** This is a genuinely large rewrite (~700 lines
   of JS render functions, 7 Swift structs, and every future config field
   addition currently costs ~30 minutes across 3 files) for a benefit
   that's mostly *future* velocity, not a user-facing feature. Sequence
   it behind user-facing roadmap items unless config-field churn is
   already a measured pain point — the cloud-providers release shipped
   fine without it, twice (once for the whole feature, once closing the
   narrower metadata-drift case), which is real evidence the mitigations
   in place (deep-merge, additive fields, phase-by-phase parity checks)
   are adequate for now.
