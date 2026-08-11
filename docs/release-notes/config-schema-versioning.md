# Config schema versioning, migrations, and the deprecation clock

**Status:** landed on `main` after **v0.5.0.1**; ships in the next release.
**Scope:** every config read and write path, plus the mesh peer warning.
This changes what is written to `config.toml` and adds a backup file.

Follows [config-write-safety.md](config-write-safety.md), which made unknown
keys survive a save. That property is unchanged and is re-proved through the
new migration rail.

## What is new for operators

### 1. `config.toml` carries `schema_version`

```toml
schema_version = 2

[agent]
...
```

An absent `schema_version` means generation **1** — that is the definition, so
every existing config is a valid generation-1 file and nothing needs to be
edited by hand. The first save after upgrading stamps `2`.

This is **not** the app version. `GET /netllm/v1/config/schema` still returns
the app version as its ETag; `schema_version` describes the shape of the file
on disk and changes only when a migration is written. See
[versioning.md](../versioning.md).

No client can set it. The dashboard Save, the macOS Settings Save and
`netllm join` all go through `apply_config_patch`, which ignores top-level
scalars; the migration runner is the only writer.

### 2. Migrations run on load, and back the file up first

The first write that would change the file's generation copies the original to
`config.toml.bak-v<n>` alongside it — **before** the write, and only once, so
the pristine pre-migration file is never overwritten by a migrated one. The
backup carries the same `0600` permissions as the config it copies.

This release ships exactly one migration, 1 → 2, and it is a **no-op**: it
adds the stamp and changes nothing else. That is asserted against the real
`config.example.toml` and against a config carrying unknown sections, unknown
fields and an unknown cloud provider
(`tests/test_config_migrations.py`).

A config written by a **newer** netllm is loaded unchanged, keeps its own
higher `schema_version`, and is reported by `netllm doctor`. An older machine
will not lower the stamp — doing so would tell the next newer agent that a
migration it needs had already run.

### 3. `netllm config migrate --dry-run`

```
netllm config migrate --dry-run          # what would change; writes nothing
netllm config migrate --dry-run --json   # same, machine-readable
netllm config migrate                    # apply now instead of on next load
```

Not required for correctness — migrations run on every load — but it lets a
rolling upgrade be rehearsed one machine at a time. See
[mesh-upgrade.md](../mesh-upgrade.md).

A config this build cannot parse is **refused**, not repaired: exit 1, the
error names the line, and the bytes on disk are untouched.

### 4. Deprecated config keys are now dated

`docs/deprecations.toml` lists every deprecated key and symbol with
`deprecated_in`, `remove_in` and `replacement`. Three things read it:

- `load_config` emits a `DeprecationWarning` naming the file, the key, the
  release it goes away in, and the remedy;
- `netllm doctor` and the dashboard's doctor panel list the deprecated keys
  present in **your** config — read from the file, not from the model, so a
  key you never set is never reported;
- CI fails once this build's version reaches a `remove_in` while the symbol
  still exists. `remove_in` is now a date rather than a comment.

Currently listed: `routing.require_same_model_for_shard` (inert since 0.4.6,
removed in 0.6.0) and `RouterPool.cached_peer_online` (removed in 0.6.0).

**Behaviour change worth knowing:** `save_config` no longer re-emits a
deprecated key that is sitting at its default value. Without this our own
writer would put the key back on every save and warn about it forever. A value
you explicitly changed is kept verbatim, warning and all — that one is
actionable.

### 5. Peer version warnings are correct and now state the support level

Two fixes in the mesh warning path:

**Prerelease ordering.** `compare_versions` scraped every digit run out of a
version string and compared the resulting list, so `0.5.0rc1` became
`[0, 5, 0, 1]` — *newer* than `0.5.0` and *exactly equal* to the real build
`0.5.0.1`. The update checker filters prereleases out, so the exposed caller
was the mesh warning: an operator running a release candidate was told to
upgrade the machine that was actually ahead, and a genuinely skewed pair
produced no warning at all. Ordering is now
`dev < alpha < beta < rc < final < 4th-component build`, pinned by
`tests/contract/version-ordering.json` and shared with the macOS app so the
two implementations cannot disagree.

**Unreadable peer versions.** A peer reporting `""`, `unknown` or anything
unparseable was treated as version `0.0.0`, which produced a confident "more
than two minors of skew, the mesh is not expected to work" about a version
nobody was running — from any device on the LAN that sent junk. It is now
reported as exactly what it is, and no skew is assessed for that peer.

Warnings also now say whether the skew is supported, degraded, or outside the
compatibility promise, instead of the same "update it" for one patch and for
two majors.

## Later generations written on this rail

### Generation 3 — stable row identity for backends and sources

Every `[[routing.backends]]` and `[[routing.sources]]` row gains a `row_id`:

```toml
[[routing.backends]]
row_id = "b-e765dd174ef1"
base_url = "http://10.0.0.5:1234/v1"
```

Purely additive — no key is removed, renamed or retyped, and a row that
already has an id (a config migrated on another machine and copied over)
keeps it. Ids are **derived** from the row's existing identity key (a
backend's `base_url`, a source's `id`) rather than random, so the migration
has a reviewable golden pair and two machines migrating the same file agree
on the result.

**Why, for operators:** without it, the save path keyed each row on a field
you can type into. Correcting a port typo on a backend erased that row's
stored `api_key` and reset `max_concurrency` to 0; renaming a source erased
its `secret`, which on a LAN bind then failed the elevated-source check on a
config you had set up correctly. Both were silent. `row_id` is never rendered
as a control and is not something to edit — the dashboard, the macOS app and
`netllm config import` all just carry it back so the agent can tell which row
an edit belongs to. A client too old to send it still merges on the old key,
so a mixed-version mesh does not regress.

## What did not change

- No `/v1/*` or `/netllm/v1/*` request-path behaviour. The 373 contract
  vectors are byte-identical and `allowed-divergences.txt` stays empty.
- Unknown-key preservation, including unknown `[cloud.providers.<id>]`
  subtrees.
- The editable section roster. `schema_version` is a top-level scalar, not a
  section: it has no form fields, no widget and no client writer.
