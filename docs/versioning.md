# Versioning — four axes, never conflated

netllm carries four independent version numbers. Conflating any two of them is
the failure this page exists to prevent, so each row says what it describes,
where it lives, and what it is **not**.

| Axis | Where | Scheme | Changes when |
|---|---|---|---|
| **App version** | `pyproject.toml`, `netllm_core.version.get_version()` | 4-part `X.Y.Z.N` | every release |
| **Config schema** | `schema_version` in `config.toml` | monotonic `int` | a migration is written |
| **Wire generation** | `/netllm/v1/status`, heartbeat, mDNS TXT | monotonic `int` | a `/netllm/v1/*` shape is removed or changed incompatibly |
| **Telemetry schema** | `telemetry.py` | monotonic `int` | the telemetry document changes shape |

## App version vs config `schema_version`

These two are the pair most likely to be confused, because both appear in
config-shaped code.

`config_schema.config_schema_document()["version"]` is `get_version()` — the
**app version**, used as an ETag so a client can tell whether its cached
description of the settings *form* is stale. It changes on every release,
including releases that change nothing about config.

`schema_version` is the **generation of the file on disk**. It changes only
when someone writes a migration, which may be several releases apart, or
never. It is owned by `netllm_core.config_migrations` and by nothing else:

- absent means generation **1**, always — that is the definition, not a
  default;
- this build writes generation `CURRENT_SCHEMA_VERSION`;
- `config_merge.apply_config_patch` ignores top-level scalars, so **no client
  Save can set it**. The migration runner is the only writer.

`tests/test_config_migrations.py::test_schema_version_is_not_the_app_version_etag`
pins the two apart.

## Where migrations run

One chokepoint: `models.load_config`, between `tomllib.loads` and
`NetllmConfig.model_validate`. Migrations are pure `dict -> dict`, so they are
unit-tested with no filesystem, and there is no second path by which a config
can reach a model without them.

Rules a migration must obey (enforced in
`tests/conformance/kit_versioning.py`):

- one step per generation, no gaps — the runner applies them in order and
  cannot bridge one;
- it must not stamp `schema_version` itself; the runner does that, so a
  migration cannot half-advance a document;
- it must not "repair" anything. A malformed document reaches pydantic exactly
  as malformed as it arrived, so the user sees the real error;
- every step ships a golden before/after pair in
  `tests/fixtures/config-generations/`.

Before the **first** migrated write, `save_config` copies the original to
`config.toml.bak-v<n>` — before, not after, and only once, so the pristine
pre-migration file is never overwritten by a migrated one.

## Rehearsing an upgrade

```
netllm config migrate --dry-run       # what would change; writes nothing
netllm config migrate                 # apply, leaving config.toml.bak-v<n>
netllm config migrate --dry-run --json
```

Migrations run automatically on load, so the command is not required for
correctness. It exists so a rolling upgrade can be rehearsed one machine at a
time — see [mesh-upgrade.md](mesh-upgrade.md).

## Version ordering

`netllm_core.update.compare_versions` and
`apps/netllm-mac/Sources/Config/VersionOrdering.swift` are two implementations
of one ordering. They share exactly one corpus,
[`tests/contract/version-ordering.json`](../tests/contract/version-ordering.json),
and both are driven from it. Add a case there, never to one implementation.

Ordering, lowest first, within one release: `dev` < `alpha`/`a` <
`beta`/`b` < `rc`/`c`/`pre`/`preview` < the final release < a 4th-component
build. So `0.5.0rc1` < `0.5.0` < `0.5.0.1`.

An unreadable version string (`""`, `"unknown"`) is **not** version 0. Ordering
degrades it to zero so it never raises, but any caller turning a comparison
into advice must first ask `is_version_like`; `service/status.py` does, and
reports what it actually saw instead of claiming the peer is two majors behind.

## Deprecations

`docs/deprecations.toml` is the generated, human-readable projection of
`netllm_core.deprecations.DEPRECATIONS`. Each row carries `deprecated_in`,
`remove_in` and `replacement`, and three things read it: a
`DeprecationWarning` from `load_config`, the deprecated-key list in
`netllm doctor`, and the CI gate that fails once this build's version reaches
`remove_in` while the symbol still exists.
