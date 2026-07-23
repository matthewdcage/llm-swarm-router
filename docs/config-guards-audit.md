# Config guards audit: current vs ideal

Prompted by a real bug hit while adding UI coverage for `routing.model_pools`'s
sibling field, `routing.model_aliases` (see [config-schema-rewrite-plan.md](config-schema-rewrite-plan.md)
for the schema-driven UI architecture these both live in): deleting a
`model_pools`/`model_aliases` entry in the macOS Settings app and hitting Save
does not actually remove it from `config.toml`. Investigating that bug found
it's one symptom of a broader inconsistency — **two independently
hand-written config-merge implementations** (CLI and web dashboard) with
different, undocumented delete/preserve guarantees per field. This doc maps
every config write path as it exists today against what it should guarantee,
so the fix can close all instances of the same bug class at once instead of
one field at a time.

## Current state

Two save paths exist, each with its own from-scratch deep-merge function that
recurses into nested dicts and otherwise replaces the patch value wholesale:

- **CLI / macOS app**: `netllm config import` → `packages/netllm-cli/src/netllm_cli/config_json.py::import_config`/`_deep_merge`. Used by `apps/netllm-mac/Sources/Config/ConfigStore.swift` (subprocess call, not HTTP — the Mac app can edit config.toml even when the agent isn't running). **Zero field-specific logic** — one generic recursive merge for everything.
- **Web dashboard**: `POST /netllm/v1/admin/config` → `packages/netllm-agent/src/netllm_agent/admin.py::apply_config_patch`/`_deep_merge_dict`. Has the *same* generic recursive merge, but additionally hand-rolls explicit rebuild-from-patch logic for four specific fields (`routing.backends`, `routing.policies`, `routing.sources`, `cloud.providers`) that preserves write-only secrets and rebuilds each list/dict from scratch keyed by an identity field.

Both were written independently and have drifted. Neither has a test that exercises deletion.

| config.toml path | Type | Delete via CLI/Mac save | Delete via dashboard save | Notes |
|---|---|---|---|---|
| `routing.backends` | `list[BackendOverride]` | ✅ (array full-replace) | ✅ (explicit rebuild, `admin.py:319-344`) | Fine on both — arrays always fully replace in the generic merge. |
| `routing.policies` | `list[RoutingPolicy]` | ✅ | ✅ (`admin.py:345-368`) | Fine on both. |
| `routing.sources` | `list[SourceConfig]` | ✅ deletes, but **no secret preservation** — see below | ✅, with secret preserved when omitted (`admin.py:369-406`) | **Divergent, not just missing.** |
| `routing.model_pools` (whole pool key) | `dict[str, ModelPool]` | ❌ (dict merge never drops an omitted key) | ❌ (falls through to generic `_deep_merge_dict`, no special-casing) | The originally reported bug. `hosts`/`models` *inside* a surviving pool are plain lists, so editing those already works — only whole-key removal is broken. |
| `routing.model_aliases` | `dict[str, list[str]]` | ❌ | ❌ | Same shape/bug as `model_pools`. |
| `cloud.providers` (whole provider key) | `dict[str, CloudProviderConfig]` | ❌ (untested path; providers are keyed to a fixed registry so end-user removal is unlikely but unguarded) | ❌ falls through the same way *if* a whole provider key were removed (enable/disable and key-set are handled separately and correctly, `admin.py:408-440`) | Low practical risk today, same latent gap. |
| `discovery.provider_urls` (per-provider key) | `dict[str, list[str]]` nested under the `discovery` catch-all | ❌ | ❌ | **Already reachable through a shipped control** — `apps/netllm-mac/Sources/AppView/SettingsViewModel.swift:411-425`'s `providerURLBinding` explicitly does `providerURLs.removeValue(forKey: provider)` when a user clears a provider's URL list to empty, then this silently fails to persist on Save. Not hypothetical. |
| `discovery`, `swarm`, `ui` (top-level sections) | raw pass-through `[String: JSONValue]` in Swift | N/A — preserving unknown keys here is *intentional* (forward-compat for Python fields with no Swift model yet) | same | Working as designed; do not "fix" these into full-replace. |

**A second, separate divergence** on `routing.sources`: `SourceConfig.secret`
is `write_only` only as a **UI-schema hint**
(`json_schema_extra={"write_only": True}` in `netllm_core/models.py`) — there
is no `field_serializer`/`model_serializer` anywhere that actually redacts it.
- The dashboard's `GET` summary (`admin.py::_source_export`) *does* manually blank it for display, and `apply_config_patch`'s hand-rolled source-rebuild preserves the stored secret whenever the patch's value is empty (`admin.py:399-404`) — correct and deliberate.
- The CLI/Mac `export_config()` calls `cfg.model_dump(mode="json")` directly with **no redaction at all** — `netllm config export` returns the real secret in plaintext, which is what `ConfigStore.load()` decodes into the Mac app's in-memory `[JSONValue]` source row. The Settings UI's `SecureField` for this row is pre-filled with the *real* value, not blank, and the comment in `SchemaFormView.swift` ("leaving this blank and saving is always safe... config_summary always returns \"\"") describes the **dashboard's** behavior, not what the Mac app's own code path actually does. Today this is harmless only because the field is never actually blank when a secret exists — if a user ever clears it, the CLI path's `_deep_merge` has no preserve-on-omission logic and would silently wipe the stored secret on Save (the Mac app currently has zero sources with a secret set, so this hasn't been hit).

No existing test (`tests/test_config_json.py`, `tests/test_cli_config_json.py`, `tests/test_admin_cloud.py`) exercises removal of a dict entry or a blanked/omitted secret round-trip for either path. `docs/config-schema-rewrite-plan.md` documents deep-merge as a preserved *guarantee*, not a known limitation — this gap was undiscovered, not deferred.

## Ideal state

One config-merge implementation, in `netllm_core` (not duplicated across
`netllm-cli` and `netllm-agent`), with the rule per field made explicit rather
than falling out of an `isinstance(value, dict)` accident:

- **Owner-controlled plain collections** (`routing.model_pools`, `routing.model_aliases`, `discovery.provider_urls`) — the editing UI always sends the complete current dict on Save, so these should **fully replace**, exactly like arrays already do. No secrets, no partial-field preservation needed — simplest possible fix, and it makes their behavior consistent with `routing.backends`/`policies` instead of a special "some dicts are different" case.
- **Owner-controlled collections with write-only fields** (`routing.sources`, `cloud.providers`) — keep the existing rebuild-from-patch-keyed-by-identity logic (already correct in `admin.py`), moved into the shared module so the CLI/Mac path gets the same secret-preservation and deletion guarantee it currently lacks.
- **True catch-all sections** (`discovery`, `swarm`, `ui` top-level) — keep recursive merge-preserving-unknown-keys; this is intentionally different from the above and should stay documented as such, not "fixed" into full-replace.

Both `netllm_cli.config_json.import_config` and `netllm_agent.admin.apply_config_patch`
call the same function, so a fix to one field's guard fixes both surfaces at
once and a future field addition only needs one classification decision, not
two hand-written implementations to keep in sync.

## Fix plan

See the implementation plan for `packages/netllm-core/src/netllm_core/config_merge.py`
(new shared module), updates to `config_json.py` and `admin.py` to delegate to
it, and the accompanying test coverage — tracked alongside this doc, not
duplicated here.
