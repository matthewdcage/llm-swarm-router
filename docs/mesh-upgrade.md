# Upgrading a netllm mesh

`netllm join` has always told operators to check for "a compatible netllm
version" without anywhere defining what that means. This page defines it.

## The promise

| Skew between two machines | Status |
|---|---|
| same minor | supported |
| **N−1 minor** | supported |
| **N−2 minor** | degraded — features on the newer side may be unavailable mesh-wide |
| beyond N−2, or any major difference | not supported |

`netllm_core.update.mesh_skew` computes this, and
`/netllm/v1/status`'s `peer_warnings` states it per peer. The classification is
symmetric: which machine is newer changes the sentence's subject, not the
support level, so two boxes in one mesh never disagree about their own skew.

Skew is **advisory**. Nothing refuses a peer on version grounds. A mesh that
partitions itself the moment someone starts an upgrade is worse than one that
tells you it is mid-upgrade.

## Order: gateway first

`swarm_tasks.py` makes the gateway authoritative for routing strategy, so a
newer gateway can serve older peers but not the reverse. Upgrade the gateway,
then the peers.

The counter-argument — gateway *last*, so the authoritative node is never
ahead — was considered and rejected: the authoritative node is the one that
has to understand the **superset**. A strategy an older peer does not know
degrades to that peer's own default, which is a routing decision nobody
wanted but nothing is corrupted by. An older gateway handed a newer peer's
state has no such fallback.

If experience contradicts this, change it here and say why — that is what this
paragraph is for.

## What makes a rolling upgrade safe

Three things, all of which are tested:

1. **Unknown config keys survive a save.** Every model under `NetllmConfig`
   allows extras, and `config_merge` carries them through. Upgrade one box,
   configure a provider there, press Save on an *older* box, and the newer
   box's keys are still in the file. Without this the five-machine scenario
   is lossy every time.
2. **A newer config is never downgraded.** A config stamped
   `schema_version = 4` loaded by a build that understands 3 is returned
   untouched, keeps its stamp, and is reported by `netllm doctor`. Lowering
   the stamp would tell the next newer agent that a migration it needs had
   already run.
3. **Migrations are backed up before the first migrated write.**
   `config.toml.bak-v<n>`, taken before the write and only once, so the
   pristine pre-migration file is never overwritten by a migrated one.

## Doing it

On each machine, gateway first:

```
netllm config migrate --dry-run     # what the upgrade will do to this file
<upgrade netllm>
netllm config migrate --dry-run     # now shows the pending migration
netllm doctor                       # deprecated keys, schema generation, peer skew
netllm config migrate               # optional — a later load does it anyway
```

`--dry-run` writes nothing at all: not the config, not a backup, not a
permission change. That is asserted in `tests/test_cli_config_migrate.py`.

Then check `GET /netllm/v1/status` on the gateway. `peer_warnings` names each
skewed peer, which machine is older, and whether that skew is supported,
degraded, or outside the promise.

## Reading a peer warning

```
peer studio-mini runs netllm 0.5.0 but this agent runs 0.5.0rc1 —
this agent is older than peer studio-mini; update it when convenient —
one minor of skew is fully supported
```

A release candidate is **older** than the release it is a candidate for. That
sentence used to come out backwards: the comparator scraped digits, read
`0.5.0rc1` as `[0, 5, 0, 1]`, and told operators running a prerelease to
upgrade the machine that was actually ahead. Ordering is now pinned by
[`tests/contract/version-ordering.json`](../tests/contract/version-ordering.json),
shared with the macOS app — see [versioning.md](versioning.md).

A peer whose version string this build cannot read is reported as exactly
that. It is **not** treated as version 0: doing so produced a confident "more
than two minors of skew" about a version nobody was running, for anything on
the LAN that sent junk.

## What is still not covered

A mixed-version mesh crossed with an upstream change: an older peer applies
its own SDK parameter allowlist, guards and aliases, and can fail
asymmetrically in a way attributed to the wrong machine. That is
`docs/extending/PROGRAM.md` §13 item 18 and is not closed by this page.
