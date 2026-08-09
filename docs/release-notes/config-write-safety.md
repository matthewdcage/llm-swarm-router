# Config write safety: unknown keys are preserved, not deleted

**Status:** landed on `main` after **v0.5.0.1**; ships in the next release.
**Scope:** every config write path. This changes user-visible write semantics.

## What was wrong

`save_config` rewrites the whole `config.toml` from `NetllmConfig.model_dump()`.
`NetllmConfig` declared no `model_config`, so pydantic's default
`extra="ignore"` applied and **every key the running agent did not know was
silently deleted on the next save**:

```
$ uv run python -c "import tomllib; from netllm_core.models import NetllmConfig; \
    print(NetllmConfig.model_validate(tomllib.loads(
      '[agent]\nlisten=\"127.0.0.1:11400\"\nfuture_field=\"keep\"\n\n[future_section]\nsetting=\"keep\"'
    )).model_dump(exclude_none=True))"
# -> future_section gone, agent.future_field gone
```

Because every writer ends in `save_config`, that applied to all of them:

- `POST /netllm/v1/admin/config` (web dashboard **Save**)
- `netllm config import` — which is the **macOS Settings Save button**
- `netllm join`

On a mixed-version mesh this is data loss in the *ordinary upgrade path*:
upgrade one machine, configure a new provider there, press Save on a machine
still running the older build, and the newer keys are gone. It is
[F-01](../architecture/07-findings-register.md)'s class generalized from
"a field the merge layer forgot to copy" to "every key this build has never
heard of".

`[cloud.providers.<id>]` had a second, explicit version of the same bug: a
validator filtered the dict down to the five registry ids, so a provider
added in a later release was deleted from the config of any older agent that
saved.

## What changed

- **Unknown keys round-trip.** Every model reachable from `config.toml` now
  derives from `netllm_core.models.ConfigModel` (`extra="allow"`). Unknown
  top-level sections, unknown fields inside known sections, and unknown
  fields on list/dict rows are carried on the model and re-emitted verbatim.
- **The merge path preserves them too.** `config_merge.apply_config_patch` —
  what the dashboard and the macOS Save button actually call — no longer
  drops a top-level section it has no model for. It also *accepts* unknown
  keys from a patch, so a newer client saving through an older agent keeps
  its own new keys.
- **Unknown cloud providers are kept and reported.** The filtering validator
  is gone. `netllm doctor` and the dashboard doctor panel now name any
  `[cloud.providers.<id>]` with no driver in this build, via the shared
  `netllm_core.config_report.unknown_cloud_provider_issues`. Nothing is
  materialized from such an entry — it is preserved but inert.
- **Preserved is not published.** Keeping a key the build cannot model means
  it must not be handed to readers either. `admin._source_export` — which
  feeds `GET /netllm/v1/config`, a route `require_read_access` leaves open
  whenever `swarm.cluster_token` is empty — was a denylist: it dumped the
  whole model and blanked only `secret`. It is now an allowlist projection
  over `SourceConfig.model_fields`, so a newer client's extras stay off the
  wire while `config_merge` still keeps them on disk. Pinned by
  `test_source_export_is_an_allowlist_not_a_denylist` and its round-trip
  sibling. This is [F-59](../architecture/10-audit-2026-08-08.md)'s class,
  which `extra="allow"` would otherwise have reintroduced.
- **One log line per save.** When a save carries keys this build does not
  model, `save_config` emits a single `WARNING` naming all of them (not one
  per key). Silent preservation would be its own trap.

## What this does *not* change

- No `schema_version`, no migrations, no deprecation clock yet — those are
  the next phase.
- No routing, request-path, or wire behaviour: the 146 golden contract
  vectors are byte-identical.
- Unknown keys are inert. Preserved is not honored: an unrecognized provider
  id builds no backend, and an unrecognized field configures nothing.

## Removed

- `netllm_core.config_schema.BOOTSTRAP_SECTIONS`. Nothing imported it —
  `dashboard.js` hand-rolls its own fallbacks and `ConfigStore.swift` reaches
  the schema through the bundled CLI, so it could never be skewed. A constant
  with one test and no caller reads as a contract while guaranteeing nothing.

## Upgrade notes

Nothing to do. The first save after upgrading may log a `WARNING` naming keys
it is preserving; that means it found keys written by a newer netllm (or a
hand-edited config) and kept them. Run `netllm doctor` if the names are
unexpected — a typo'd `[cloud.providers.*]` id is now reported rather than
quietly deleted, which is the one case where the old behaviour looked tidier.

## Verify

```bash
uv run pytest tests/test_config_forward_compat.py -q   # the new contract
uv run pytest tests/contract -q                        # 146 vectors, unchanged
./scripts/ci.sh lint
```
