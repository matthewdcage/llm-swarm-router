"""Stable per-row identity for the two config lists a client can edit.

`[[routing.backends]]` and `[[routing.sources]]` used to be keyed, on the
save path, by a field the user can type into: a backend by its `base_url`,
a source by its `id`. That made "edit that field" indistinguishable from
"delete this row and create a different one" -- and because `api_key` and
`secret` are write-only (never sent to a client, so never sent back), the
freshly-created row came back with them blank. Fixing a port typo on a
backend erased its API key and its `max_concurrency`; renaming a source
erased its secret, which on a LAN bind then hard-fails the elevated-source
guard in `config_guards`.

The fix is an identity that is *not* a user-visible field: `row_id`. It is
opaque, server-assigned, `read_only` in the schema document (so neither
generic form renders a control for it) and `identity` in the schema document
(so both patch builders carry it back verbatim -- see
`schemaItemToPatch` in dashboard.js).

Ids are **derived, not random**. A random uuid4 would be equally unique, but
derivation buys two properties that matter here:

* the 2 -> 3 migration has a golden before/after pair like every other
  migration in this tree, which a random id makes impossible to write; and
* two agents that migrate *the same* config.toml (the ordinary way a mesh is
  set up -- copy the file, or restore the same backup on two machines) agree
  on the ids, instead of diverging the moment each one loads.

The seed is the row's old identity key, so the id a migration assigns is the
id the pre-migration merge was already keying on. Collisions (two rows with
the same base_url, which nothing forbids) get a `-2`, `-3`, ... suffix
against the ids already taken in that same list.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from itertools import count

__all__ = [
    "BACKEND_ROW_PREFIX",
    "SOURCE_ROW_PREFIX",
    "derive_row_id",
]

# Prefixes exist so a row_id read out of a config.toml or a log line says
# what kind of row it belongs to. They carry no semantics; nothing parses
# them back out.
BACKEND_ROW_PREFIX = "b"
SOURCE_ROW_PREFIX = "s"

# 6 bytes -> 12 hex chars. Long enough that an accidental collision across a
# realistic list (tens of rows) is not a thing, short enough that the id fits
# on one line of a TOML file next to the URL it identifies.
_DIGEST_BYTES = 6


def derive_row_id(prefix: str, seed: str, taken: Iterable[str] = ()) -> str:
    """A stable opaque id for one row, unique within `taken`.

    `seed` is the row's user-visible identity key at the moment the id is
    minted (a backend's base_url, a source's id). It is only ever a seed:
    once minted, the id never changes again, which is the entire point --
    the user is free to edit the field the seed came from.
    """
    used = set(taken)
    digest = hashlib.blake2s(
        seed.encode("utf-8", "surrogatepass"), digest_size=_DIGEST_BYTES
    ).hexdigest()
    candidate = f"{prefix}-{digest}"
    if candidate not in used:
        return candidate
    for suffix in count(2):
        numbered = f"{candidate}-{suffix}"
        if numbered not in used:
            return numbered
    raise AssertionError("unreachable")  # pragma: no cover
