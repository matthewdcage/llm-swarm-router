"""Axis E conformance kit: the version, migration and deprecation contracts.

Parameterized over `DEPRECATIONS` and `MIGRATIONS`, so a new entry in either
registry acquires this suite with no test-file edit — the same property
`kit_cloud` and `kit_local` have.

The load-bearing test here is `test_an_expired_deprecation_has_actually_been
_removed`. `remove_in` is a promise printed to users in `netllm doctor` and in
a `DeprecationWarning`; without a gate it is a comment. With one, the release
that reaches the date cannot be cut while the symbol is still there.
"""

from __future__ import annotations

import importlib
import tomllib
from typing import Any

import pytest
from netllm_core.config_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
)
from netllm_core.deprecations import DEPRECATIONS, Deprecation
from netllm_core.update import compare_versions, is_version_like, mesh_skew
from netllm_core.version import get_version
from pydantic import BaseModel

from conformance.projections import REPO_ROOT

DEPRECATIONS_TOML = REPO_ROOT / "docs/deprecations.toml"

KINDS = {"config-key", "symbol"}


@pytest.fixture(params=DEPRECATIONS, ids=[d.id for d in DEPRECATIONS])
def deprecation(request: pytest.FixtureRequest) -> Deprecation:
    return request.param


@pytest.fixture(
    params=MIGRATIONS, ids=[f"v{m.from_version}-to-v{m.to_version}" for m in MIGRATIONS]
)
def migration(request: pytest.FixtureRequest) -> Migration:
    return request.param


# --- the deprecation clock ------------------------------------------------


def _resolve(symbol: str) -> Any:
    """Resolve `module:Dotted.Path`, or raise LookupError.

    Pydantic model fields are not class attributes, so a config-key entry is
    resolved through `model_fields` — otherwise every config-key deprecation
    would look already-removed and the gate would never fire.
    """
    module_name, _, dotted = symbol.partition(":")
    if not dotted:
        raise ValueError(f"{symbol!r} is not module:attr")
    try:
        node: Any = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - a typo in the registry
        raise LookupError(f"no module {module_name}") from exc
    for index, part in enumerate(dotted.split(".")):
        if (
            isinstance(node, type)
            and issubclass(node, BaseModel)
            and part in node.model_fields
        ):
            return node.model_fields[part]
        if not hasattr(node, part):
            resolved_so_far = ".".join(dotted.split(".")[:index]) or module_name
            raise LookupError(f"{resolved_so_far} has no attribute {part!r}")
        node = getattr(node, part)
    return node


def test_an_expired_deprecation_has_actually_been_removed(
    deprecation: Deprecation,
) -> None:
    """THE gate. Once this build's version reaches `remove_in`, the thing has
    to be gone — or this test names it and the release does not ship.

    To see it fire: set `remove_in` to a past release in
    `netllm_core/deprecations.py`, run
    `python3 scripts/generate-registry-artifacts.py`, and run this test.
    """
    if compare_versions(get_version(), deprecation.remove_in) < 0:
        pytest.skip(
            f"{deprecation.id} is not due until {deprecation.remove_in} "
            f"(this build is {get_version()})"
        )
    try:
        _resolve(deprecation.symbol)
    except LookupError:
        return  # removed on time
    pytest.fail(
        f"{deprecation.id} promised removal in netllm {deprecation.remove_in} "
        f"and this build is {get_version()}, but {deprecation.symbol} still "
        f"exists. Delete it and drop the registry row, or move `remove_in` "
        f"in a commit that also writes the release note explaining the slip."
    )


def test_a_deprecation_that_is_not_due_yet_still_resolves(
    deprecation: Deprecation,
) -> None:
    """The other half of the gate, and the reason it is trustworthy.

    A registry row pointing at a symbol that was already deleted would make
    the expiry test pass forever without proving anything. Both directions are
    asserted, so the row is either live or gone — never stale cover.
    """
    if compare_versions(get_version(), deprecation.remove_in) >= 0:
        pytest.skip("already due; the expiry test owns this row")
    _resolve(deprecation.symbol)


def test_deprecation_row_is_well_formed(deprecation: Deprecation) -> None:
    assert deprecation.id, "every row needs a stable id"
    assert deprecation.kind in KINDS, f"{deprecation.id}: unknown kind"
    assert ":" in deprecation.symbol, (
        f"{deprecation.id}: symbol must be module:attr so the gate can resolve it"
    )
    assert len(deprecation.notes) > 60, (
        f"{deprecation.id}: notes are shown verbatim in `netllm doctor`; a "
        "label is not an explanation"
    )
    assert compare_versions(deprecation.deprecated_in, deprecation.remove_in) < 0, (
        f"{deprecation.id}: remove_in must be strictly after deprecated_in"
    )
    if deprecation.kind == "config-key":
        assert deprecation.config_path, (
            f"{deprecation.id}: a config-key row needs config_path, or doctor "
            "cannot match it against the user's file"
        )
    else:
        assert not deprecation.config_path, (
            f"{deprecation.id}: only config-key rows carry config_path"
        )


def test_a_config_key_deprecation_names_a_field_that_exists(
    deprecation: Deprecation,
) -> None:
    """`config_path` has to address the real config, or doctor reports nothing.

    Resolved against a default `NetllmConfig` dump rather than the model tree,
    because that dump is exactly what `save_config` writes and what
    `deprecated_keys_in_document` walks.
    """
    if deprecation.kind != "config-key":
        pytest.skip("not a config key")
    from netllm_core.models import NetllmConfig

    node: Any = NetllmConfig().model_dump(mode="json")
    parts = deprecation.config_path.split(".")
    for part in parts[:-1]:
        assert isinstance(node, dict) and part in node, (
            f"{deprecation.id}: config_path {deprecation.config_path} does not "
            f"exist (stopped at {part!r})"
        )
        node = node[part]
    assert isinstance(node, dict) and parts[-1] in node, (
        f"{deprecation.id}: config_path {deprecation.config_path} does not exist"
    )


def test_the_generated_doc_matches_the_registry() -> None:
    """`docs/deprecations.toml` is the human-readable projection of the
    registry. `scripts/ci.sh lint` regenerates and diffs it; this asserts the
    *content* round-trips, so a lint that was skipped locally still fails."""
    rows = tomllib.loads(DEPRECATIONS_TOML.read_text(encoding="utf-8"))["deprecation"]
    assert [row["id"] for row in rows] == [d.id for d in DEPRECATIONS]
    for row, entry in zip(rows, DEPRECATIONS, strict=True):
        for field in ("kind", "config_path", "deprecated_in", "remove_in"):
            assert row[field] == getattr(entry, field), (
                f"{entry.id}: docs/deprecations.toml {field} is stale — run "
                "python3 scripts/generate-registry-artifacts.py"
            )
        assert " ".join(row["notes"].split()) == " ".join(entry.notes.split())


def test_the_deprecation_ledger_tripwire_is_not_tripped() -> None:
    """Same discipline as `mirrors.toml`: past a handful of live deprecations
    the release cadence is the problem, not the ledger's size."""
    live = [
        entry
        for entry in DEPRECATIONS
        if compare_versions(get_version(), entry.remove_in) < 0
    ]
    assert len(live) <= 8, (
        f"{len(live)} deprecations are outstanding. That is a backlog, not a "
        "clock — ship the removals rather than adding rows."
    )


# --- the migration rail ---------------------------------------------------


def test_migration_step_is_well_formed(migration: Migration) -> None:
    assert migration.to_version == migration.from_version + 1
    assert migration.notes.strip()
    assert callable(migration.apply)


def test_migration_is_idempotent_on_its_own_output(migration: Migration) -> None:
    """Running a step twice must not compound.

    A migrated config can be handed to the rail again (a load after a save, a
    `config migrate` after an agent already migrated), and `pending_migrations`
    is the only thing stopping a second pass. Prove the step itself is safe
    even if that guard is ever wrong.
    """
    document: dict[str, Any] = {
        "agent": {"hostname": "kit"},
        "routing": {"default_strategy": "local_first"},
        "future_section": {"k": 1},
    }
    once = migration.apply(dict(document))
    twice = migration.apply(dict(once))
    assert twice == once


def test_current_schema_version_is_the_end_of_the_chain() -> None:
    assert MIGRATIONS[-1].to_version == CURRENT_SCHEMA_VERSION


# --- mesh skew ------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "level"),
    [
        ("0.5.0", "0.5.3", "supported"),
        ("0.5.0", "0.6.0", "supported"),
        ("0.6.0", "0.5.0", "supported"),
        ("0.5.0", "0.7.0", "degraded"),
        ("0.5.0", "0.8.0", "unsupported"),
        ("0.5.0", "1.5.0", "unsupported"),
        ("0.5.0rc1", "0.5.0", "supported"),
    ],
)
def test_mesh_skew_matches_the_compatibility_promise(
    left: str, right: str, level: str
) -> None:
    """N-1 supported, N-2 degraded, beyond that not supported
    (docs/mesh-upgrade.md). Symmetric: which machine is newer changes the
    advice text's subject, not the support level."""
    assert mesh_skew(left, right).level == level
    assert mesh_skew(right, left).level == level


def test_mesh_skew_never_raises_on_a_peer_reported_version() -> None:
    """The peer's version is data another machine on the LAN controls."""
    for junk in ["", "unknown", "v", "0.5.0-something-weird", "🙂", "9" * 400]:
        assert mesh_skew(get_version(), junk).level in {
            "supported",
            "degraded",
            "unsupported",
        }


# --- peer-controlled input: containment, found by adversarial review --------


def _hostile_id(value: object) -> str:
    """A short, stable id for each payload.

    pytest's generated id is the payload itself, and pytest exports the full
    node id in ``PYTEST_CURRENT_TEST``. On Windows an environment variable is
    capped at 32767 characters, so the 100000-character case errored at setup
    AND teardown -- a green suite everywhere else and a red one on
    windows-latest. Naming the cases keeps the payloads intact and the ids
    small.
    """
    if isinstance(value, str):
        head = value[:12].encode("unicode_escape").decode("ascii")
        return f"str[{len(value)}]:{head}" if len(value) > 12 else f"str:{head!r}"
    return f"{type(value).__name__}:{value!r}"[:40]


@pytest.mark.parametrize(
    "hostile",
    [
        "9" * 4400,  # over CPython's 4300-digit int() cap
        "9" * 100000,
        "0xdeadbeef",
        "0abc",
        "0 DROP TABLE peers",
        "0" + "." * 5000,
        "\x00\x01\x02",
        "🙂" * 100,
        "",
        "unknown",
        "v",
        None,
        ["1.0.0"],
        {"version": "1.0.0"},
        42,
        3.14,
    ],
    ids=_hostile_id,
)
def test_a_peer_cannot_raise_out_of_the_version_comparator(hostile: object) -> None:
    """`version` comes verbatim from another machine's heartbeat JSON.

    `mesh_skew` is reached from `peer_config_warnings`, which
    `status_payload` calls unconditionally — so ANY exception here is a
    remote denial of service on `GET /netllm/v1/status`, triggerable by
    anything that can talk to this agent.

    Two real holes, both found by adversarial review rather than by CI:
    an unbounded `\\d+` let a 4400-digit string reach `int()`, which CPython
    refuses to convert; and a non-string (untyped JSON gives lists and dicts)
    raised `TypeError` out of `is_version_like`.
    """
    assert is_version_like(hostile) in (True, False)
    skew = mesh_skew(get_version(), hostile)  # type: ignore[arg-type]
    assert skew.level in ("supported", "degraded", "unsupported")
    compare_versions(get_version(), hostile)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "junk", ["0xdeadbeef", "0abc", "0 DROP TABLE", "9" * 4400, "", "unknown"]
)
def test_junk_is_never_reported_as_a_version_number(junk: str) -> None:
    """The half-fix this replaces was worse than no claim.

    `is_version_like` used `match`, so anything *starting* with a digit
    qualified. A peer reporting `0xdeadbeef` had its leading `0` read as the
    release, and the node announced "more than two minors of skew — the mesh
    is not expected to work" about a version nobody runs. Anchoring at both
    ends is what makes the guard mean what it says.
    """
    assert is_version_like(junk) is False


@pytest.mark.parametrize("real", ["0.4.0", "0.5.0.1", "1.0.0rc1", " v0.5.0 ", "1.0"])
def test_real_versions_still_pass_the_guard(real: str) -> None:
    """Anchoring must not reject versions the mesh actually reports."""
    assert is_version_like(real) is True
