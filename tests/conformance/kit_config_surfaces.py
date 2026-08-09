"""Every config row model must be carried in full by every editing surface.

`config_merge` rebuilds some row types from the model's defaults plus whatever
the patch sends, because those rows have no stable identity key. That makes an
omission on a client destructive rather than inert: a field the Swift struct
does not declare is not "left alone" on Save, it is **erased**.

That is exactly how `RoutingPolicy.source` was lost -- silently widening a
source-scoped routing policy to every caller, which is F-01
(`docs/architecture/07-findings-register.md`) reintroduced through a client.
It was found by an adversarial audit rather than by CI, twice. These tests are
the CI answer: they parse the real Swift source and fail naming the missing
field.

Deliberately a projection test rather than a Swift unit test -- `swift test`
does not run in CI (only `swift build`, inside the macos-14 menubar-lifecycle
job), so an assertion that lives in Swift would not guard anything.
"""

from __future__ import annotations

import re

import pytest
from netllm_core.models import BackendOverride, RoutingPolicy, SourceConfig

from conformance.projections import REPO_ROOT

SWIFT_DOC = "apps/netllm-mac/Sources/Config/NetllmConfigDocument.swift"

# Fields a client legitimately does not carry, with the reason. Anything not
# listed here must appear in the Swift struct -- "we forgot" is not a reason.
INTENTIONALLY_ABSENT: dict[tuple[str, str], str] = {
    ("BackendOverride", "api_key"): (
        "write-only; the Swift app sends it through the Keychain path and "
        "config_merge preserves the stored value when it is omitted"
    ),
    ("SourceConfig", "secret"): (
        "write-only, same contract as BackendOverride.api_key -- "
        "ConfigStore.blankSourceSecret relies on empty-preserves-stored"
    ),
}


def _swift_struct_fields(struct_name: str) -> tuple[set[str], str]:
    """Declared `var <name>` properties of a Swift struct, plus its location.

    Computed properties (`var id: String { ... }`) are excluded -- they are
    derived, not encoded, so they are not part of the wire shape.
    """
    path = REPO_ROOT / SWIFT_DOC
    text = path.read_text(encoding="utf-8")
    start = text.find(f"struct {struct_name}")
    assert start != -1, f"{SWIFT_DOC}: struct {struct_name} not found"
    depth = 0
    end = start
    for index in range(text.find("{", start), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    body = text[start:end]
    fields = {
        match.group(1)
        for match in re.finditer(r"^\s*var\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.M)
        if "{" not in body.split(match.group(0))[1].split("\n")[0]
    }
    line = text.count("\n", 0, start) + 1
    return fields, f"{path.name}:{line}"


# Row types WITHOUT an identity key. config_merge rebuilds these from the
# model's defaults, so a field a client omits is ERASED, not preserved. These
# are the destructive ones and the surface must carry every field.
IDENTITYLESS = [(RoutingPolicy, "RoutingPolicy")]

# Row types WITH an identity key (BackendOverride keys on base_url,
# SourceConfig on id). config_merge seeds each row from the prior model dump,
# so omission is non-destructive -- the field simply is not editable from that
# surface, which is an Axis D parity question, not data loss. Asserted as
# non-destructive rather than as present.
IDENTITY_KEYED = [
    (BackendOverride, "BackendOverride", "base_url"),
]


@pytest.mark.parametrize(
    ("model", "struct_name"), IDENTITYLESS, ids=[n for _, n in IDENTITYLESS]
)
def test_identityless_swift_struct_carries_every_model_field(
    model: type, struct_name: str
) -> None:
    try:
        swift_fields, location = _swift_struct_fields(struct_name)
    except AssertionError:
        pytest.skip(f"{struct_name} has no Swift counterpart yet")

    expected = set(model.model_fields)
    excused = {
        field
        for (owner, field) in INTENTIONALLY_ABSENT
        if owner == struct_name and field in expected
    }
    missing = expected - swift_fields - excused
    assert not missing, (
        f"{location}: Swift {struct_name} is missing {sorted(missing)}. "
        f"This row has no identity key, so config_merge rebuilds it from "
        f"defaults and an omitted field is ERASED on the next Save. Add it to "
        f"the struct, or declare it in INTENTIONALLY_ABSENT with a reason."
    )


@pytest.mark.parametrize(
    ("model", "struct_name", "key_field"),
    IDENTITY_KEYED,
    ids=[n for _, n, _ in IDENTITY_KEYED],
)
def test_identity_keyed_omission_is_non_destructive(
    model: type, struct_name: str, key_field: str
) -> None:
    """What actually protects the fields a client does not carry.

    Swift's BackendOverride omits `cloud_provider` and `max_concurrency` --
    the very two fields F-01 was filed about. That is safe ONLY because these
    rows merge onto the prior row. Pin the property rather than trusting it,
    so a future change to the merge strategy fails here instead of silently
    re-opening F-01.
    """
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import NetllmConfig

    swift_fields, location = _swift_struct_fields(struct_name)
    omitted = set(model.model_fields) - swift_fields
    if not omitted:
        pytest.skip(f"{struct_name} carries every field; nothing to protect")

    cfg = NetllmConfig()
    if struct_name == "BackendOverride":
        stored = BackendOverride(
            base_url="http://10.0.0.5:1234/v1",
            cloud_provider="openai",
            max_concurrency=7,
        )
        cfg.routing.backends = [stored]
        section = "backends"
    else:
        stored = SourceConfig(id="cursor", max_concurrency=7)
        cfg.routing.sources = [stored]
        section = "sources"

    before = stored.model_dump(mode="json")
    patch_row = {k: v for k, v in before.items() if k in swift_fields}
    assert key_field in patch_row, f"{location}: identity key not carried"
    merged = apply_config_patch(cfg, {"routing": {section: [patch_row]}})
    after = getattr(merged.routing, section)[0].model_dump(mode="json")

    for field in sorted(omitted):
        assert after[field] == before[field], (
            f"{location}: Swift {struct_name} omits {field!r} and the merge "
            f"DESTROYED it ({before[field]!r} -> {after[field]!r}). This row "
            f"is supposed to merge onto the prior row via {key_field!r}."
        )


def test_the_excuse_list_only_excuses_real_fields() -> None:
    """An excuse for a field that no longer exists is stale cover."""
    owners = {
        "RoutingPolicy": RoutingPolicy,
        "BackendOverride": BackendOverride,
        "SourceConfig": SourceConfig,
    }
    for (owner, field), reason in INTENTIONALLY_ABSENT.items():
        assert owner in owners, f"unknown model in excuse list: {owner}"
        assert field in owners[owner].model_fields, (
            f"{owner}.{field} is excused but no longer exists -- delete the entry"
        )
        assert len(reason) > 30, f"{owner}.{field} needs a real reason, not a label"


def test_routing_policy_source_survives_a_swift_shaped_save() -> None:
    """The regression itself, end to end through the real merge path.

    A Swift-shaped patch (only the fields that struct declares) must not erase
    `source`. This is the assertion that would have caught the original F-01
    and its reintroduction.
    """
    from netllm_core.config_merge import apply_config_patch
    from netllm_core.models import NetllmConfig

    cfg = NetllmConfig()
    cfg.routing.policies = [
        RoutingPolicy(name="cursor-only", model_prefix="gpt", source="cursor")
    ]
    swift_fields, _ = _swift_struct_fields("RoutingPolicy")
    patch_row = {
        key: value
        for key, value in cfg.routing.policies[0].model_dump(mode="json").items()
        if key in swift_fields
    }
    merged = apply_config_patch(cfg, {"routing": {"policies": [patch_row]}})
    assert merged.routing.policies[0].source == "cursor", (
        "a Save from the macOS app widened a source-scoped policy to every "
        "caller -- the exact F-01 regression"
    )


def test_swift_carries_sources_as_an_untyped_passthrough() -> None:
    """`routing.sources` has no Swift struct, and that is the safe choice.

    NetllmConfigDocument declares `var sources: [JSONValue]`, so the app
    round-trips whatever the agent sent it verbatim -- it cannot drop a field
    it has never heard of, which is the same forward-compatibility property
    `extra="allow"` gives the Python models. Pin it: replacing this with a
    typed struct would silently re-introduce the drop, and the field roster
    test above would not catch it because there would be no struct to compare.
    """
    path = REPO_ROOT / SWIFT_DOC
    text = path.read_text(encoding="utf-8")
    assert "var sources: [JSONValue]" in text, (
        f"{path.name}: routing.sources is no longer an untyped pass-through. "
        "If it is now a typed struct, add it to IDENTITYLESS or IDENTITY_KEYED "
        "so its field roster is guarded."
    )


# --- ledger discipline ----------------------------------------------------

PHASE_ORDER = [
    "phase-0",
    "phase-1",
    "phase-2",
    "phase-3",
    "phase-4",
    "phase-5a",
    "phase-5b",
    "phase-6",
    "phase-7",
    "phase-8",
]


def _ledger() -> dict:
    import tomllib

    path = REPO_ROOT / "tests/conformance/ledgers/mirrors.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _entries():
    for fact_class in _ledger()["fact_class"]:
        for entry in fact_class.get("allowed_mirrors", []):
            yield fact_class["id"], entry


def test_every_ledger_entry_has_a_real_reason_and_expiry() -> None:
    """`expires` was read by nothing.

    check-registry-mirrors.py mentions it only in a docstring, so an entry
    could carry any string -- or an expiry that had already passed -- and stay
    green. An unenforced expiry is decoration, and the ledger's whole claim is
    that a mirror is temporary and dated.
    """
    valid = set(PHASE_ORDER)
    for class_id, entry in _entries():
        where = f"{class_id}:{entry['glob']}"
        reason = entry.get("reason", "")
        assert len(reason) > 40, (
            f"{where}: reason is too thin to justify a mirror: {reason!r}"
        )
        expires = entry.get("expires", "")
        assert expires, f"{where}: no expiry"
        assert expires in valid or expires.startswith("never"), (
            f"{where}: expiry {expires!r} is neither a known phase {sorted(valid)} "
            "nor an explicit 'never — <reason>'"
        )


def test_no_ledger_entry_is_overdue() -> None:
    """An entry whose phase has already shipped must be closed, not carried."""
    current = _ledger()["scan"]["current_phase"]
    assert current in PHASE_ORDER, f"unknown current_phase {current!r}"
    done_through = PHASE_ORDER.index(current)
    overdue = [
        f"{class_id}:{entry['glob']} (expires {entry['expires']})"
        for class_id, entry in _entries()
        if entry.get("expires", "") in PHASE_ORDER
        and PHASE_ORDER.index(entry["expires"]) <= done_through
    ]
    assert not overdue, (
        "these mirrors were due to be removed by the phase this tree has "
        f"completed ({current}):\n  " + "\n  ".join(overdue) + "\n"
        "Close them, or re-date them with the reason the phase did not."
    )


def test_the_ledger_tripwire_is_not_yet_tripped() -> None:
    """PROGRAM.md: 5+ exceptions means the spec is wrong, not the ledger short.

    Recorded as an executable limit rather than prose, per the program's own
    ledger-discipline section.
    """
    per_class: dict[str, int] = {}
    for class_id, _ in _entries():
        per_class[class_id] = per_class.get(class_id, 0) + 1
    for class_id, count in per_class.items():
        assert count <= 6, (
            f"{class_id} carries {count} mirror exceptions. Past ~5 the "
            "registry shape is wrong -- redesign it rather than adding entries."
        )
