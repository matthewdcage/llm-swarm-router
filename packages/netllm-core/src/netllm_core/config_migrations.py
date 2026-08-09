"""Ordered config.toml migrations (PROGRAM.md §3 Axis E.2/E.3).

This module runs against **every user's real config**, on every load, before
anything is validated. A bug here corrupts data people care about, so the
shape is deliberately small and paranoid:

* **Pure dict -> dict.** No filesystem, no models, no network. Every migration
  is unit-testable in isolation and `tests/fixtures/config-generations/` holds
  a golden before/after pair per migration.
* **One chokepoint.** `models.load_config` calls `migrate_document` between
  `tomllib.loads` and `NetllmConfig.model_validate`, and nothing else runs
  migrations. There is no second path a config can arrive by.
* **Never downgrades.** A config stamped *newer* than this build is returned
  untouched, with `schema_version` intact. Rewriting it to this build's number
  would tell the next, newer, agent that a migration it needs has already run —
  the mixed-version mesh failure this whole phase exists to prevent.
* **Never repairs.** A migration transforms shape, it does not validate. A
  malformed document is handed to pydantic exactly as malformed as it arrived,
  so the error the user sees is the real one and not a migration traceback.

`schema_version` is NOT `config_schema.get_version()`. The two are different
axes and conflating them is the mistake this file must not make (PROGRAM.md
§4): `get_version()` is the **app version**, served as an ETag on
`GET /netllm/v1/config/schema` so a client can tell whether its cached form
description is stale. `schema_version` is a **monotonic integer describing the
shape of config.toml on disk** — it changes only when a migration is written,
which may be several app releases apart or never. Nothing may derive one from
the other.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "Migration",
    "MIGRATIONS",
    "MigrationResult",
    "document_schema_version",
    "migrate_document",
    "pending_migrations",
]

SCHEMA_VERSION_KEY = "schema_version"

# A config written before this key existed. Absent => 1, always; that is the
# definition, not a default, and every consumer must agree on it.
LEGACY_SCHEMA_VERSION = 1

# The shape this build writes. Bump ONLY together with a new Migration.
CURRENT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Migration:
    """One step from one on-disk shape to the next.

    `apply` receives the document and must return the transformed document.
    It must not mutate its argument (`migrate_document` deep-copies before
    the first step, so mutation would corrupt only the copy — but a migration
    that relies on that is a migration that breaks when the pipeline changes).
    It must not stamp `schema_version`; the runner does that, so a migration
    cannot half-advance the version.
    """

    from_version: int
    to_version: int
    apply: Callable[[dict[str, Any]], dict[str, Any]]
    notes: str


def _v1_to_v2(document: dict[str, Any]) -> dict[str, Any]:
    """Identity.

    Generation 2 exists to put a version stamp on the file, not to change it.
    Every pre-2 config is already a valid generation-2 config, so this is a
    genuine no-op — asserted against the real `config.example.toml` and against
    a config carrying unknown keys in `tests/test_config_migrations.py`.

    It is here rather than "skipped because it does nothing" because the rail
    is the deliverable: the first migration that *does* something must not also
    be the first migration that ever runs on a user's machine.
    """
    return document


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        from_version=1,
        to_version=2,
        apply=_v1_to_v2,
        notes=(
            "Stamp schema_version. No key is added, removed, renamed or "
            "retyped; an unstamped config and a schema_version = 2 config "
            "describe the same settings."
        ),
    ),
)


@dataclass(frozen=True)
class MigrationResult:
    """What `migrate_document` did, for the CLI and for `save_config`."""

    document: dict[str, Any]
    from_version: int
    to_version: int
    applied: tuple[Migration, ...]
    #: The document is stamped NEWER than this build understands. Nothing was
    #: applied and nothing was rewritten.
    from_the_future: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.applied)


def document_schema_version(document: dict[str, Any]) -> int:
    """The generation of a parsed config.toml. Absent or unreadable => 1.

    A non-integer or negative value is treated as legacy rather than raising:
    the alternative is a config that cannot be loaded at all because someone
    typed `schema_version = "2"`, and refusing to start is a worse outcome
    than running the migrations (which are no-ops on an already-current file).
    """
    raw = document.get(SCHEMA_VERSION_KEY)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return LEGACY_SCHEMA_VERSION
    if raw < LEGACY_SCHEMA_VERSION:
        return LEGACY_SCHEMA_VERSION
    return raw


def pending_migrations(version: int) -> tuple[Migration, ...]:
    """Migrations that still have to run for a document at `version`."""
    return tuple(m for m in MIGRATIONS if m.from_version >= version)


def migrate_document(document: dict[str, Any]) -> MigrationResult:
    """Run every pending migration. Pure: the argument is never mutated."""
    version = document_schema_version(document)

    if version > CURRENT_SCHEMA_VERSION:
        # Written by a newer netllm. Returned verbatim — including its own
        # schema_version — so this build neither downgrades the stamp nor
        # pretends to understand the shape. `extra="allow"` on the models is
        # what lets the unknown keys survive the save that may follow.
        return MigrationResult(
            document=document,
            from_version=version,
            to_version=version,
            applied=(),
            from_the_future=True,
        )

    steps = pending_migrations(version)
    if not steps:
        return MigrationResult(
            document=document,
            from_version=version,
            to_version=version,
            applied=(),
        )

    working = copy.deepcopy(document)
    for step in steps:
        working = step.apply(working)
        if not isinstance(working, dict):  # pragma: no cover - defensive
            raise TypeError(
                f"migration {step.from_version}->{step.to_version} returned "
                f"{type(working).__name__}, not a dict"
            )
        working[SCHEMA_VERSION_KEY] = step.to_version

    return MigrationResult(
        document=working,
        from_version=version,
        to_version=steps[-1].to_version,
        applied=steps,
    )
