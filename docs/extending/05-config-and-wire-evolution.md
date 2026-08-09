# Evolving the config and the wire (Axis E)

> **Half of this axis is built and half is not, and the difference matters
> more here than anywhere else in this document set.**
>
> **Built (Phase 2):** unknown keys survive a load → save → load round-trip,
> in both directions, on every write path. This was the only actively
> destructive bug in the whole inventory.
>
> **Not built (Phase 6):** `schema_version`, `config_migrations.py`,
> `docs/deprecations.toml`, `docs/versioning.md`, `docs/mesh-upgrade.md`, the
> mixed-version test lane, the shared version-ordering corpus. None of these
> files exist in this tree. If you came here looking for a migration to write,
> **there is no migration rail to write it into yet.** See
> [../compatibility-policy.md](../compatibility-policy.md) for exactly which
> promises are enforced today and which are aspirations.

## Adding a config field

Follow [04-cli-and-control-plane.md](04-cli-and-control-plane.md) — the
parity obligation is the demanding part, not the pydantic field.

## Adding a config **section**

Add the model to `NetllmConfig`, and to `SECTIONS` in
`packages/netllm-core/src/netllm_core/config_schema.py`. The roster is
asserted three ways against `NetllmConfig.model_fields`, so a section present
in one and absent from the other fails immediately.

If the section holds a `dict[str, X]` field, classify it as **full-replace**
or **deep-merge** in `packages/netllm-core/src/netllm_core/config_merge.py`.
That choice is genuine semantics and [PROGRAM.md](PROGRAM.md) §6.5 refuses to
synthesize it — but the *completeness* of the classification is gated, so a
new dict field that is in neither tuple fails by name.

The distinction is not cosmetic. `routing.model_pools` is full-replace
because its owning UI always sends the whole map, so a deleted pool has to
survive the round trip as deleted. `cloud.providers` is deep-merge because it
holds write-only API keys the client never echoes back; full-replacing it
would wipe every key on every save.

## The forward-compatibility contract (built)

- `NetllmConfig` and each section model allow extras. `load_config` keeps
  them, `apply_config_patch` carries them, `save_config` re-emits them and
  logs one warning naming what it preserved.
- `[cloud.providers.<id>]` subtrees for providers this build does not know
  are **preserved and reported by `doctor`**, not filtered away. The old
  filtering validator deleted a newer release's provider from an older
  agent's config on the next save.
- A patch from a newer client carrying keys this build does not model is
  accepted rather than rejected.

That is what makes a rolling upgrade non-lossy: upgrade one machine,
configure a provider there, press **Save** on an older machine, and the newer
machine's keys are still there.

## Checklist

Rows marked ***unguarded*** have no test behind them; rows marked
***not built*** describe machinery that does not exist in this tree.

| # | Step | Guard |
|---|---|---|
| 1 | New section appears in `NetllmConfig`, `SECTIONS` and the schema document | `tests/test_config_forward_compat.py::test_section_roster_three_way_equality` |
| 2 | Every model field has a schema entry | `tests/test_config_schema.py::test_every_pydantic_field_has_a_schema_entry` |
| 3 | Every `dict[str, X]` section field is classified full-replace or deep-merge | `tests/test_config_forward_compat.py::test_every_section_dict_field_is_classified` |
| 4 | Unknown section survives load → save | `tests/test_config_forward_compat.py::test_unknown_section_survives_load_save` |
| 5 | Unknown field in a known section survives load → save | `…::test_unknown_field_in_known_section_survives_load_save` |
| 6 | Known values are not disturbed by preservation | `…::test_load_save_does_not_disturb_known_values` |
| 7 | Preserved keys are named, once, in a warning | `…::test_preserved_extra_paths_names_every_unknown_key`, `…::test_save_config_logs_one_warning_naming_preserved_keys` |
| 8 | No warning when there is nothing to preserve | `…::test_no_warning_when_config_has_no_unknown_keys` |
| 9 | Unknown keys survive `apply_config_patch` | `…::test_unknown_keys_survive_apply_config_patch` |
| 10 | A patch from a newer client is accepted | `…::test_apply_config_patch_accepts_unknown_keys_from_a_newer_client` |
| 11 | **An older agent's save preserves a newer agent's keys** | `…::test_older_agent_save_preserves_newer_agent_keys` |
| 12 | Unknown cloud-provider subtree preserved, and reported by doctor | `…::test_unknown_cloud_provider_subtree_is_preserved`, `…::test_doctor_reports_unknown_cloud_provider_instead_of_deleting_it` |
| 13 | Merge allowlists derived from the models, not hand-listed | `…::test_merge_sources_allowlist_matches_source_config_fields`, `…::test_merge_cloud_providers_allowlist_matches_provider_config_fields` |
| 14 | Source export is an allowlist, and a redacted row round-trips | `…::test_source_export_is_an_allowlist_not_a_denylist`, `…::test_a_redacted_source_row_round_trips_without_data_loss` |
| 15 | Every field editable/derived/ledgered on every surface | `tests/conformance/kit_config_surfaces.py::test_every_config_field_has_a_disposition_on_every_surface` |
| 16 | `config.example.toml` still parses | `tests/test_contract.py` |
| — | **Renaming or removing a config key** | **not built** — there is no `schema_version`, no `config_migrations.py`, no `.bak-v{n}`, no `netllm config migrate`. Phase 6 is unlanded. Today the only safe change is an **additive** one |
| — | **Deprecating a config key with a clock** | **not built** — `docs/deprecations.toml` does not exist; no `DeprecationWarning` on load, and `doctor` lists no deprecated keys |
| — | **Wire-generation number on `/netllm/v1/*`** | **not built** — status, heartbeat and mDNS TXT carry no wire generation. Only the telemetry schema has a version integer (`telemetry.py`) |
| — | **Mixed-version mesh behaviour** | **not built** — no `NETLLM_COMPAT_PRETEND_VERSION` lane, no `tests/contract/interop/`. The only mixed-version coverage is heartbeat backend rows in `tests/test_contract.py` |
| — | **Version-ordering agreement between Python and Swift** | **not built** — two algorithms, no shared corpus. Python's `compare_versions` mis-orders prereleases; it is masked only because `fetch_latest_release` filters them, so an operator *running* a prerelease is the exposed case |
| — | **`config.example.toml` documents every schema field** | **unguarded** — only parseability is asserted, plus the cloud stanza roster added in Phase 8 |
| — | **Your default is a good default** | **unguarded** |

## Run it

Axis E has no per-entry kit — there is nothing to parameterize over until
`MIGRATIONS` exists. Run the whole rail:

```bash
uv run pytest tests/test_config_forward_compat.py tests/test_config_schema.py \
             tests/test_config_merge.py tests/conformance/kit_config_surfaces.py -q
```
