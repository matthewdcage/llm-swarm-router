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

Deliberately a projection test rather than a Swift unit test. `swift test`
now runs in CI (added to the macos-14 menubar-lifecycle job in the same
change), but a Swift assertion could only compare the struct against a
hardcoded field list -- another mirror. Parsing the struct from Python lets
the assertion compare it against the pydantic model itself, which is the
actual source of truth.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass

import pytest
from netllm_core.config_schema import config_schema_document
from netllm_core.control_plane import CONTROLS
from netllm_core.models import BackendOverride, RoutingPolicy, SourceConfig

from conformance.projections import (
    REPO_ROOT,
    attribute_values,
    dict_keys_after,
    dict_values_after,
    source_region,
)


def _swift_filter_names(rel_path: str, marker: str) -> frozenset[str]:
    """Field names the Swift call site filters OUT, read from Swift itself.

    This used to be a hand-written `frozenset` restating the literals in
    `SettingsWindowView`'s filter array -- a mirror, sitting inside the gate
    whose whole purpose is to delete mirrors. Adversarial review priced it:
    growing the Swift array to also drop `max_concurrency`, `strategy` and
    `allow_cloud` removed three real macOS controls and the entire suite
    stayed GREEN, because the Python copy still insisted only three names
    were excluded.

    Derived now, so widening the Swift filter reclassifies those fields as
    absent and the parity gate reports them instead of excusing them.
    """
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    start = text.find(marker)
    assert start != -1, f"{rel_path}: filter marker not found: {marker!r}"
    # Anchor past the closure brace: the marker line also contains the empty
    # `?? []` coalesce, and taking the first `[` after it silently yields an
    # empty exclusion set -- which would read as "nothing is excluded" and
    # quietly turn three absent fields into covered ones.
    brace = text.find("{", start)
    assert brace != -1, f"{rel_path}: no closure after {marker!r}"
    open_at = text.find("[", brace)
    close_at = text.find("]", open_at)
    assert open_at != -1 and close_at != -1, f"{rel_path}: unterminated filter list"
    names = frozenset(re.findall(r'"([^"]+)"', text[open_at:close_at]))
    assert names, f"{rel_path}: filter array at {marker!r} is empty"
    return names


SWIFT_DOC = "apps/netllm-mac/Sources/Config/NetllmConfigDocument.swift"

# Fields a client legitimately does not carry, with the reason. Anything not
# listed here must appear in the Swift struct -- "we forgot" is not a reason.
INTENTIONALLY_ABSENT: dict[tuple[str, str], str] = {
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
        for (owner, field) in _row_field_excuses()
        if owner == struct_name and field in expected
    }
    missing = expected - swift_fields - excused
    assert not missing, (
        f"{location}: Swift {struct_name} is missing {sorted(missing)}. "
        f"This row has no identity key, so config_merge rebuilds it from "
        f"defaults and an omitted field is ERASED on the next Save. Add it to "
        f"the struct, or add a [[row_field]] row to {CONTROL_LEDGER} with a\n"
        f"reason and an expiry."
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
    for (owner, field), reason in _row_field_excuses().items():
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
        # A phase may carry a trailing " — why", which is strictly better than
        # a bare phase; `never` must carry one.
        head = expires.split("—")[0].split("--")[0].strip()
        assert head in valid or head == "never", (
            f"{where}: expiry {expires!r} is neither a known phase "
            f"{sorted(valid)} nor an explicit 'never — <reason>'"
        )
        if head == "never":
            assert head != expires, f"{where}: 'never' must state why"


def test_no_ledger_entry_is_overdue() -> None:
    """An entry whose phase has already shipped must be closed, not carried."""
    current = _ledger()["scan"]["current_phase"]
    assert current in PHASE_ORDER, f"unknown current_phase {current!r}"
    done_through = PHASE_ORDER.index(current)
    overdue = [
        f"{class_id}:{entry['glob']} (expires {entry['expires']})"
        for class_id, entry in _entries()
        if entry.get("expires", "").split("—")[0].split("--")[0].strip() in PHASE_ORDER
        and PHASE_ORDER.index(entry["expires"].split("—")[0].split("--")[0].strip())
        <= done_through
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


# ==========================================================================
# Axis D — cross-surface control parity (docs/extending/08-control-parity.md)
# ==========================================================================
#
# F-21's own prescribed fix, made executable: "add a drift test that asserts
# every NetllmConfig field is either schema-rendered or explicitly listed in a
# KNOWN_UNRENDERED allowlist -- so adding a Python field forces a conscious
# client decision."
#
# It is a coverage DISPOSITION, not a registry. Every schema key resolves to
# exactly one of four answers per editing surface:
#
#   derived          read_only; both generic renderers drop it by construction
#                    (schemaFieldsCard's filter, SchemaFormView.swift's
#                    `filter { !($0.readOnly ?? false) }`), so it is absent on
#                    every surface correctly and forever and is excluded from
#                    the parity denominator
#   schema_rendered  covered by that surface's generic schema form -- the field
#                    name never appears in the source, so "present" is
#                    unassertable by name and the renderer call is the evidence
#   hand_rendered    the name appears as a quoted key or a property access
#                    inside the hand-written renderer for that subtree
#   ledgered         declared absent in ledgers/control-parity.toml with a
#                    reason and an expiry, under the same discipline tests as
#                    every other ledger in this tree

SWIFT_SETTINGS = "apps/netllm-mac/Sources/AppView/SettingsWindowView.swift"
SWIFT_VIEWMODEL = "apps/netllm-mac/Sources/AppView/SettingsViewModel.swift"
SWIFT_CLOUD = "apps/netllm-mac/Sources/AppView/CloudSettingsView.swift"
DASHBOARD_JS = "packages/netllm-agent/src/netllm_agent/static/dashboard.js"
INDEX_HTML = "packages/netllm-agent/src/netllm_agent/static/index.html"
CONTROL_LEDGER = "tests/conformance/ledgers/control-parity.toml"


@dataclass(frozen=True)
class Slice:
    """One contiguous span of a surface file, anchored on identifiers."""

    path: str
    start: str
    end: str | None = None


@dataclass(frozen=True)
class Region:
    """The source that renders one config subtree on one surface.

    `mode` answers landmine 2 of this phase: `SettingsWindowView.swift`
    renders all ten `ui.*` fields through one `SchemaFormView(fields:
    uiFields)` and the names never appear in Swift at all, so "present" is
    unassertable by name for a schema-driven section. `generic` makes that a
    first-class disposition whose evidence is the renderer call itself.
    """

    slices: tuple[Slice, ...]
    mode: str = "hand"  # "hand" | "generic"
    marker: str = ""  # required when mode == "generic"; asserted present
    excludes: frozenset[str] = frozenset()  # names the generic form skips
    nested_marker: str = ""  # generic evidence for keys below a collection


@dataclass(frozen=True)
class EditingSurface:
    name: str
    regions: dict[str, Region]


DASHBOARD = EditingSurface(
    name="dashboard",
    regions={
        "agent": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderAgentTab",
                    "function renderDiscoveryTab",
                ),
            )
        ),
        "discovery": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderDiscoveryTab",
                    "function renderSwarmTab",
                ),
            )
        ),
        "swarm": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderSwarmTab",
                    "const SCHEMA_ITEM_FACTORIES",
                ),
            ),
            mode="generic",
            marker='renderSchemaForm("swarm"',
        ),
        "routing": Region(
            (
                Slice(
                    DASHBOARD_JS, "function renderRoutingTab", "function knownHostRefs"
                ),
            ),
            # Scalars are named explicitly in an ordered array; every
            # collection's item_schema goes through the generic widgets.
            nested_marker="renderSchemaField(byName",
        ),
        "routing.sources": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderSourcesTab",
                    "function renderHarnessCard",
                ),
            ),
            mode="generic",
            marker="renderSchemaField(byName.sources",
            nested_marker="renderSchemaField(byName.sources",
        ),
        "ui": Region(
            (Slice(DASHBOARD_JS, "function renderUiTab", "async function loadLogs"),),
            mode="generic",
            marker='renderSchemaForm("ui"',
        ),
        "cloud": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderCloudTab",
                    "function renderCloudProviderCard",
                ),
            ),
            mode="generic",
            marker='renderSchemaForm("cloud"',
            # `providers: { hidden: true }` -- a per-provider card needs live
            # registry metadata (display name, regions, api_key_set) the
            # shape-only schema does not carry. Own region below.
            excludes=frozenset({"providers"}),
        ),
        "cloud.providers": Region(
            (
                Slice(
                    DASHBOARD_JS,
                    "function renderCloudProviderCard",
                    "function cloudModelEnabled",
                ),
                Slice(
                    DASHBOARD_JS, "function cloudModelEnabled", "function renderUiTab"
                ),
            )
        ),
    },
)

MACOS = EditingSurface(
    name="macos",
    regions={
        "agent": Region(
            (Slice(SWIFT_SETTINGS, "private var agentTab", "private var discoveryTab"),)
        ),
        "discovery": Region(
            (
                Slice(
                    SWIFT_SETTINGS, "private var discoveryTab", "private var swarmTab"
                ),
                # discoveryTab calls providerEnabled/providerURLBinding; the
                # config keys themselves are bound on the view model.
                Slice(SWIFT_VIEWMODEL, "func providerURLBinding", "func runDiscover"),
                Slice(SWIFT_VIEWMODEL, "func toggleProvider", "func addModelAlias"),
            )
        ),
        "swarm": Region(
            (Slice(SWIFT_SETTINGS, "private var swarmTab", "private var routingTab"),)
        ),
        "routing": Region(
            (
                Slice(
                    SWIFT_SETTINGS,
                    "private var routingTab",
                    "private func modelAliasEditor",
                ),
            )
        ),
        "routing.policies": Region(
            (
                Slice(
                    SWIFT_SETTINGS,
                    "private func routingPolicyEditor",
                    "private func backendOverrideEditor",
                ),
            )
        ),
        "routing.backends": Region(
            (
                Slice(
                    SWIFT_SETTINGS,
                    "private func backendOverrideEditor",
                    "private func backendRow",
                ),
            )
        ),
        "routing.model_pools": Region(
            (
                Slice(
                    SWIFT_SETTINGS,
                    "private func modelPoolEditor",
                    "private var unregisteredHarnessesSection",
                ),
            ),
            mode="generic",
            marker="SchemaFormView(",
        ),
        "routing.sources": Region(
            (
                Slice(
                    SWIFT_SETTINGS, "private func sourceEditor", "private var cloudTab"
                ),
            ),
            mode="generic",
            marker="SchemaFormView(",
            # sourceEditor filters these out before handing the item schema to
            # SchemaFormView: it has no dict_strings/object/dict-of-object
            # widget, and the text fallback binds a TextField to `.stringValue`
            # (nil for those), so typing would overwrite the object with a
            # plain string. Declared here so the exclusion is asserted rather
            # than silently read as coverage -- the literals in that filter
            # array are the names of fields NOT rendered.
            excludes=_swift_filter_names(
                SWIFT_SETTINGS, "let renderedFields = (sourceFields ?? []).filter"
            ),
        ),
        "ui": Region(
            (
                Slice(
                    SWIFT_SETTINGS, "private var uiTab", "private func pushUiSettings"
                ),
            ),
            mode="generic",
            marker="SchemaFormView(",
        ),
        "cloud": Region(
            (
                Slice(
                    SWIFT_CLOUD, "struct CloudSettingsView", "struct CloudProviderInfo"
                ),
            )
        ),
        "cloud.providers": Region(
            (Slice(SWIFT_CLOUD, "private struct CloudProviderCard", None),)
        ),
    },
)

EDITING_SURFACES = (DASHBOARD, MACOS)


def _schema_keys() -> list[tuple[str, dict]]:
    """Every schema key as a dotted path, `[]` marking a collection item."""
    out: list[tuple[str, dict]] = []

    def walk(prefix: str, fields: list[dict]) -> None:
        for spec in fields:
            key = f"{prefix}.{spec['name']}"
            out.append((key, spec))
            if spec.get("item_schema"):
                walk(f"{key}[]", spec["item_schema"])

    for section, body in config_schema_document()["sections"].items():
        walk(section, body["fields"])
    return out


SCHEMA_KEYS = _schema_keys()
SCHEMA_SPECS = dict(SCHEMA_KEYS)
DERIVED_KEYS = frozenset(key for key, spec in SCHEMA_KEYS if spec.get("read_only"))
#: The parity denominator: every key a generic renderer would render.
PARITY_KEYS = tuple(key for key, _ in SCHEMA_KEYS if key not in DERIVED_KEYS)


def _control_ledger() -> dict:
    return tomllib.loads((REPO_ROOT / CONTROL_LEDGER).read_text(encoding="utf-8"))


def _covers(entry_key: str, key: str) -> bool:
    """A ledger entry covers its key and everything nested beneath it.

    One entry for `routing.sources[].scenarios` therefore also answers for its
    five child fields, so the ledger states the decision once instead of six
    times. Rows are still counted per key when the percentage is measured.
    """
    return (
        key == entry_key
        or key.startswith(f"{entry_key}[]")
        or key.startswith(f"{entry_key}.")
    )


def _ledger_entry_for(surface: str, key: str) -> dict | None:
    for entry in _control_ledger().get("field", []):
        if entry["surface"] == surface and _covers(entry["key"], key):
            return entry
    return None


def _names(text: str, leaf: str) -> bool:
    """`leaf` used as a quoted key or a property access, not as prose.

    A comment or a sentence naming a field is not that field: dashboard.js
    contains the string "an above-default max_concurrency" in help text, and a
    naive substring scan reads that as a control. Comments are stripped by
    `source_region`; this adds the shape requirement.

    Word-bounded on purpose -- `.api_key` must not match `.api_key_env`, which
    is how a hand-written editor carrying only the env-var field reads as
    carrying the secret field too.
    """
    escaped = re.escape(leaf)
    return re.search(rf'"{escaped}"|\.{escaped}\b', text) is not None


def _owning_region(surface: EditingSurface, key: str) -> str | None:
    """The most specific declared region that renders `key`."""
    best: str | None = None
    for name in surface.regions:
        if key == name or key.startswith(f"{name}.") or key.startswith(f"{name}[]"):
            if best is None or len(name) > len(best):
                best = name
    return best


def _region_text(surface: EditingSurface, name: str) -> tuple[str, str]:
    parts = [
        source_region(s.path, s.start, s.end) for s in surface.regions[name].slices
    ]
    return "\n".join(part.text for part in parts), parts[0].location


def _excluded_ancestor(key: str, region_name: str, excluded: frozenset[str]) -> bool:
    tail = key[len(region_name) :].lstrip(".")
    if tail.startswith("[]."):
        tail = tail[3:]
    head = tail.split("[]")[0].split(".")[0]
    return head in excluded


def disposition(surface: EditingSurface, key: str) -> tuple[str, str]:
    """`(disposition, evidence)` for one schema key on one editing surface.

    Returns `("absent", why)` rather than raising, so the parameterized test
    is the single place a missing control becomes a named failure.
    """
    if key in DERIVED_KEYS:
        return "derived", "read_only; dropped by both generic renderers"

    region_name = _owning_region(surface, key)
    if region_name is None:
        return "absent", f"no {surface.name} region renders {key!r}"

    region = surface.regions[region_name]
    text, location = _region_text(surface, region_name)
    leaf = key.rsplit(".", 1)[-1]

    # A collection with its own region IS the control: the region is that
    # subtree's editor, so the collection key needs no separate mention.
    if region_name == key:
        return "hand_rendered", f"{location} renders {key}"

    direct_child = key in (f"{region_name}.{leaf}", f"{region_name}[].{leaf}")

    if direct_child and leaf in region.excludes:
        # Named in the renderer's *exclusion list*, which is the opposite of
        # rendered: the literal is there precisely because the field is not.
        return "absent", f"{location}: {leaf!r} is filtered out of the form"
    if not direct_child and _excluded_ancestor(key, region_name, region.excludes):
        return "absent", f"{location}: nested under a field the form filters out"

    marker = region.marker if direct_child else region.nested_marker
    if not direct_child and not marker and region.mode == "generic":
        marker = region.marker
    if marker:
        assert marker in text, (
            f"{location}: {surface.name} claims to render {region_name!r} "
            f"through the generic schema form, but {marker!r} is gone. Restore "
            f"the generic renderer, or hand-render every field it covered."
        )
        return "schema_rendered", f"{location} via {marker}"

    if _names(text, leaf):
        return "hand_rendered", f"{location} names {leaf!r}"
    return "absent", f"{location} does not name {leaf!r}"


# --- the parity gate ------------------------------------------------------


@pytest.mark.parametrize("key", PARITY_KEYS)
def test_every_config_field_has_a_disposition_on_every_surface(key: str) -> None:
    """Adding a config field forces a conscious client decision — F-21's fix.

    A new pydantic field lands here as `absent` on both surfaces and fails by
    name, with the file and line of the renderer that would have to carry it.
    """
    for surface in EDITING_SURFACES:
        answer, evidence = disposition(surface, key)
        if answer != "absent":
            continue
        entry = _ledger_entry_for(surface.name, key)
        assert entry is not None, (
            f"{key} has no control on the {surface.name} surface: {evidence}. "
            f"Render it, or declare it in {CONTROL_LEDGER} with a reason and "
            f"an expiry:\n"
            f'  [[field]]\n  surface = "{surface.name}"\n  key = "{key}"\n'
            f'  reason = "..."\n  expires = "phase-8"'
        )


def test_read_only_fields_are_derived_on_every_surface() -> None:
    """The five read_only keys are absent everywhere, correctly and forever.

    Both generic renderers drop them by construction, so counting them as
    parity failures would open with five permanent ledger rows and a
    distorted percentage. They are excluded from the denominator instead --
    pinned here so a change to either filter is visible.
    """
    assert DERIVED_KEYS, "no read_only fields found — did the schema hints move?"
    js = source_region(
        DASHBOARD_JS, "function schemaFieldsCard", "function renderSchemaForm"
    )
    assert "!f.read_only" in js.text, f"{js.location}: the read_only filter is gone"
    swift = source_region(
        "apps/netllm-mac/Sources/AppView/SchemaFormView.swift",
        "struct SchemaFormView",
        "private struct SchemaFieldRow",
    )
    assert "readOnly" in swift.text, f"{swift.location}: the read_only filter is gone"
    for key in DERIVED_KEYS:
        for surface in EDITING_SURFACES:
            answer, _ = disposition(surface, key)
            assert answer == "derived", f"{key} on {surface.name} is {answer}"


def test_the_control_parity_ledger_is_under_the_tripwire() -> None:
    """PROGRAM.md §7: >20% intentionally-absent means the spec is wrong.

    Recorded as an executable limit, against a stated denominator. Measuring
    one ControlDescriptor per config field is what failed this tripwire in the
    first place (22.2% with the CLI excluded, 62% with it required), which is
    why descriptors are per tab/feature and field parity is a disposition --
    see docs/extending/08-control-parity.md.
    """
    total = len(PARITY_KEYS) * len(EDITING_SURFACES)
    ledgered = sum(
        1
        for surface in EDITING_SURFACES
        for key in PARITY_KEYS
        if _ledger_entry_for(surface.name, key) is not None
    )
    assert ledgered / total <= 0.20, (
        f"{ledgered}/{total} = {ledgered / total:.1%} of (field, surface) pairs "
        "are ledgered as intentionally absent. Past 20% the shape is wrong: "
        "redesign the surfaces, do not add entries."
    )


def test_no_ledger_entry_excuses_a_field_that_is_actually_rendered() -> None:
    """An excuse for a control that now exists is stale cover.

    The mirror ledger has the same rule; without it a ledger only ever grows,
    and closing a gap leaves the excuse behind as apparent evidence the gap
    is still open.
    """
    stale = []
    for entry in _control_ledger().get("field", []):
        surface = next(s for s in EDITING_SURFACES if s.name == entry["surface"])
        covered = [k for k in PARITY_KEYS if _covers(entry["key"], k)]
        assert covered, (
            f"{entry['surface']}:{entry['key']} matches no schema key — "
            "the field was renamed or removed; delete the entry"
        )
        rendered = [k for k in covered if disposition(surface, k)[0] != "absent"]
        if len(rendered) == len(covered):
            stale.append(f"{entry['surface']}:{entry['key']} — fully rendered")
        elif rendered:
            # An entry excusing BOTH absent and rendered keys banks cover it
            # has not earned. The old rule only flagged an entry when EVERY
            # covered key was rendered, so one genuinely-absent key kept an
            # arbitrarily broad excuse alive: adversarial review consolidated
            # three narrow rows into `routing.sources` and silently
            # pre-excused 13 rendered macOS keys, suite green, still under
            # the tripwire. Excuses must be no broader than the gap.
            stale.append(
                f"{entry['surface']}:{entry['key']} — excuses "
                f"{len(rendered)} key(s) that ARE rendered "
                f"({', '.join(sorted(rendered)[:4])}"
                f"{', …' if len(rendered) > 4 else ''}); narrow the key"
            )
    assert not stale, (
        "these ledger entries excuse controls that are rendered:\n  "
        + "\n  ".join(stale)
    )


# --- ControlDescriptor projections ----------------------------------------


def _ledgered_control(surface: str, key: str) -> dict | None:
    for entry in _control_ledger().get("control", []):
        if entry["surface"] == surface and entry["unit"] == key:
            return entry
    return None


CONTROL_IDS = [c.key for c in CONTROLS]


def test_descriptors_are_served_additively_on_the_schema_endpoint() -> None:
    """No new endpoint, no client break (PROGRAM.md §3 Axis D)."""
    doc = config_schema_document()
    assert "sections" in doc and "version" in doc, "the pre-existing shape moved"
    served = {c["key"] for c in doc["controls"]}
    assert served == set(CONTROL_IDS)


@pytest.mark.parametrize("control", CONTROLS, ids=CONTROL_IDS)
def test_control_exists_on_the_dashboard(control) -> None:
    if "dashboard" not in control.surfaces_required:
        pytest.skip(f"{control.key} does not claim the dashboard")
    if _ledgered_control("dashboard", control.key):
        pytest.skip("ledgered absent")
    renderers = dict_values_after(DASHBOARD_JS, "const TAB_RENDERERS")
    tabs = dict_keys_after(DASHBOARD_JS, "const TAB_RENDERERS")
    assert len(tabs.values) > 1, (
        f"{tabs.location}: the TAB_RENDERERS parse collapsed to "
        f"{len(tabs.values)} entries — the object literal shape changed"
    )
    if control.is_tab:
        assert control.key in tabs.values, f"{tabs.location} has no tab {control.key!r}"
        renderers.assert_covers({control.dashboard_renderer})
        attribute_values(INDEX_HTML, "data-tab").assert_covers({control.key})
        attribute_values(INDEX_HTML, "id", prefix="tab-").assert_covers(
            {f"tab-{control.key}"}
        )
    body = source_region(DASHBOARD_JS)
    assert f"function {control.dashboard_renderer}(" in body.text, (
        f"dashboard.js defines no {control.dashboard_renderer}()"
    )


@pytest.mark.parametrize("control", CONTROLS, ids=CONTROL_IDS)
def test_control_exists_on_macos(control) -> None:
    if "macos" not in control.surfaces_required:
        pytest.skip(f"{control.key} does not claim the macOS app")
    if _ledgered_control("macos", control.key):
        pytest.skip("ledgered absent")
    # Per-surface presence unit: `sources` is a tab on the web and a
    # `sectionHeader("Sources")` inside routingTab on macOS. A tab-set diff
    # calls that absent; a human calls it present, and the human is right.
    settings = source_region(SWIFT_SETTINGS)
    viewmodel = source_region(SWIFT_VIEWMODEL)
    assert (
        control.swift_symbol in settings.text or control.swift_symbol in viewmodel.text
    ), (
        f"{settings.location}: the macOS app has no {control.swift_symbol} "
        f"for control {control.key!r}"
    )


@pytest.mark.parametrize("control", CONTROLS, ids=CONTROL_IDS)
def test_action_controls_have_a_real_cli_command(control) -> None:
    """Step 4: assert what is actually true about the CLI.

    `cli` is NOT required per config field — only 2 of 50 field names appear
    as Typer options and both are runtime flags, so demanding field-level CLI
    parity would mandate ~30 options nobody asked for. What is true is that
    every *action* has a command, and that any command a descriptor names is
    real. Both are asserted by real Typer introspection rather than a grep.
    """
    if "cli" not in control.surfaces_required and not control.cli:
        pytest.skip(f"{control.key} names no CLI command")
    if "cli" in control.surfaces_required:
        assert control.cli, (
            f"{control.key} is an action requiring the CLI but names no command"
        )
    commands = cli_commands()
    missing = [name for name in control.cli if name not in commands]
    assert not missing, (
        f"netllm_cli.main exposes no {missing} for control "
        f"{control.key!r}; it has {sorted(commands)[:5]}…"
    )


@pytest.mark.parametrize("control", CONTROLS, ids=CONTROL_IDS)
def test_declared_admin_route_exists(control) -> None:
    """Only discriminating for non-config controls.

    All 99 config fields go through `POST /netllm/v1/admin/config`, so
    asserting that route for a config control is a tautology — the field is
    left empty there and this test has nothing to check.
    """
    if not control.admin_route:
        pytest.skip(f"{control.key} declares no admin route")
    import json

    routes = json.loads(
        (REPO_ROOT / "tests/contract/routes.json").read_text(encoding="utf-8")
    )
    paths = {r["path"] for r in routes["routes"]}
    assert control.admin_route in paths, (
        f"routes.json has no {control.admin_route} for control {control.key!r}"
    )


def cli_commands() -> dict[str, list[str]]:
    """Every leaf command and its long options, by real Typer introspection.

    `typer.main.get_command(app)` returns a `typer.core.TyperGroup`, which
    under typer 0.26 / click 8.4 is **not** a `click.Group` subclass:
    `isinstance(cmd, click.Group)` is False and silently collapses the whole
    tree to a single leaf. Duck-typing on `.commands` is the fix, and
    `test_the_cli_walk_does_not_collapse` is the regression guard.
    """
    from netllm_cli.main import app
    from typer.main import get_command

    def walk(cmd, prefix: str = "") -> dict[str, list[str]]:
        subs = getattr(cmd, "commands", None)  # duck-typed, NOT isinstance
        if subs:
            out: dict[str, list[str]] = {}
            for name, sub in sorted(subs.items()):
                out.update(walk(sub, f"{prefix} {name}".strip()))
            return out
        opts: list[str] = []
        for param in cmd.params:
            opts += list(getattr(param, "opts", []) or [])
            opts += list(getattr(param, "secondary_opts", []) or [])
        return {prefix: sorted(o for o in opts if o.startswith("--"))}

    return walk(get_command(app))


def test_the_cli_walk_does_not_collapse() -> None:
    """The isinstance trap, pinned.

    A walk that used `isinstance(cmd, click.Group)` returns exactly one leaf
    and every CLI assertion above then passes vacuously.
    """
    commands = cli_commands()
    assert len(commands) > 1, (
        f"the Typer walk found {len(commands)} command(s) — it collapsed. "
        "TyperGroup is not a click.Group; duck-type on `.commands`."
    )
    assert "config export" in commands and "config import" in commands


def test_config_import_export_round_trips_every_field() -> None:
    """The CLI's real config obligation, in place of per-field flags.

    `netllm config export | netllm config import` is what makes the CLI a
    complete config surface without ~30 options nobody asked for. Asserted on
    a config with every section populated, through the real commands.
    """
    import json
    import tempfile
    from pathlib import Path

    from netllm_cli.main import app
    from netllm_core.models import (
        CloudProviderConfig,
        NetllmConfig,
        load_config,
        save_config,
    )
    from typer.testing import CliRunner

    cfg = NetllmConfig()
    cfg.agent.max_concurrency = 7
    cfg.routing.upstream_read_timeout_s = 900.0
    cfg.routing.upstream_connect_timeout_s = 12.5
    cfg.routing.model_aliases = {"house": ["qwen3-coder", "gpt-oss-120b"]}
    cfg.ui.model_favorites = ["qwen3-coder"]
    cfg.cloud.providers = {
        "openai": CloudProviderConfig(
            enabled=True, api_key_env="MY_KEY", base_url="https://proxy.example/v1"
        )
    }

    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "config.toml"
        target = Path(tmp) / "target.toml"
        save_config(cfg, source)
        save_config(NetllmConfig(), target)

        exported = runner.invoke(app, ["config", "export", "--config", str(source)])
        assert exported.exit_code == 0, exported.output
        payload = json.loads(exported.stdout)

        imported = runner.invoke(
            app,
            ["config", "import", "--config", str(target)],
            input=json.dumps(payload),
        )
        assert imported.exit_code == 0, imported.output
        round_tripped = load_config(target)

    before = cfg.model_dump(mode="json")
    after = round_tripped.model_dump(mode="json")
    lost = {
        key: (before[key], after[key]) for key in before if before[key] != after[key]
    }
    assert not lost, (
        "netllm config export -> import did not round-trip these sections: "
        f"{sorted(lost)}"
    )


# --- ledger discipline for the control ledger -----------------------------


def _control_ledger_entries():
    ledger = _control_ledger()
    for entry in ledger.get("control", []):
        yield f"control {entry['surface']}:{entry['unit']}", entry
    for entry in ledger.get("field", []):
        yield f"field {entry['surface']}:{entry['key']}", entry
    for entry in ledger.get("row_field", []):
        yield f"row_field {entry['model']}.{entry['field']}", entry


def test_every_control_ledger_entry_has_a_real_reason_and_expiry() -> None:
    """Same discipline as mirrors.toml, applied to the parity ledger.

    Moving `INTENTIONALLY_ABSENT` out of Python and into this file is what
    buys that for free: a dict literal in a test module carries no expiry and
    nothing could ever check one.
    """
    valid = set(PHASE_ORDER)
    for where, entry in _control_ledger_entries():
        reason = entry.get("reason", "")
        assert len(reason) > 40, (
            f"{where}: reason is too thin to justify an absent control: {reason!r}"
        )
        expires = entry.get("expires", "")
        assert expires, f"{where}: no expiry"
        head = expires.split("—")[0].split("--")[0].strip()
        assert head in valid or head == "never", (
            f"{where}: expiry {expires!r} is neither a known phase "
            f"{sorted(valid)} nor an explicit 'never — <reason>'"
        )
        if head == "never":
            assert head != expires, f"{where}: 'never' must state why"


def test_no_control_ledger_entry_is_overdue() -> None:
    current = _control_ledger()["scan"]["current_phase"]
    assert current in PHASE_ORDER, f"unknown current_phase {current!r}"
    done_through = PHASE_ORDER.index(current)
    overdue = []
    for where, entry in _control_ledger_entries():
        head = entry["expires"].split("—")[0].split("--")[0].strip()
        if head in PHASE_ORDER and PHASE_ORDER.index(head) <= done_through:
            overdue.append(f"{where} (expires {entry['expires']})")
    assert not overdue, (
        "these controls were due by the phase this tree has completed "
        f"({current}):\n  " + "\n  ".join(overdue)
    )


# --- absorbed from the ad-hoc predecessors --------------------------------
#
# These assertions used to live in tests/test_dashboard_config_schema.py and
# tests/test_dashboard_ui_wiring.py as standalone greps. They are the same
# question this kit answers -- "does the control exist on this surface" -- and
# leaving them where they were would mean two systems asking it, which is the
# duplication PROGRAM.md §1 exists to stop. Their originals are deleted, not
# copied; each file carries a pointer here.


def test_the_generic_schema_machinery_exists_on_both_surfaces() -> None:
    """Every `schema_rendered` disposition rests on these functions.

    The per-region evidence assertions in `disposition()` cover the three
    `renderSchemaForm(...)` call sites. This covers the widgets and the patch
    builder underneath them, which no single field maps to but every nested
    field depends on -- and pins the superseded hand-written editors as gone,
    not merely unused.
    """
    js = source_region(DASHBOARD_JS)
    for marker in (
        "function renderSchemaForm",
        "function renderSchemaField",
        "function schemaListOfObjectsRow",
        "function schemaDictOfObjectsRow",
        "function schemaDictListStringsRow",
        "function schemaNestedObjectRow",
        "function buildSchemaSectionPatch",
        "function schemaItemToPatch",
        "SCHEMA_ITEM_FACTORIES",
        "loadConfigSchema",
        "/netllm/v1/config/schema",
    ):
        assert marker in js.text, f"dashboard.js: missing {marker!r}"
    for dead in (
        "function renderRoutingPoliciesEditor",
        "function renderBackendOverridesEditor",
    ):
        assert dead not in js.text, f"dashboard.js: dead code still present: {dead!r}"

    swift = source_region(
        "apps/netllm-mac/Sources/AppView/SchemaFormView.swift", "struct SchemaFormView"
    )
    assert "SchemaFieldRow(field: field" in swift.text, (
        f"{swift.location}: SchemaFormView no longer dispatches per field"
    )


def test_drain_is_wired_end_to_end_on_the_dashboard() -> None:
    """The `drain` descriptor's dashboard leg, beyond symbol presence.

    POST /netllm/v1/admin/drain shipped with no UI on either surface: the only
    dashboard path to take an agent out of rotation was stopping it, which
    kills in-flight requests -- the exact thing drain exists to avoid.
    """
    html = (REPO_ROOT / INDEX_HTML).read_text(encoding="utf-8")
    js = source_region(DASHBOARD_JS)
    assert 'id="btn-drain"' in html, "no drain control in index.html"
    assert "btn-drain" in js.text, "the drain control is not bound to a handler"
    assert "/netllm/v1/admin/drain" in js.text, "the drain handler calls no endpoint"


# --- a control must be FED, not merely present -----------------------------

# Fields whose control was added by closing a real gap, paired with how to
# reach their value in `config_summary`. A control bound to a value the
# summary never sends renders empty and POSTs "" back -- and because
# config_merge treats an explicit "" as a value, that ERASES what was stored.
# The Phase 7 commit had to fix exactly that in `_cloud_provider_export`;
# adversarial review then reverted BOTH new exports and the full 1415-test
# suite stayed green, because every other gate proves only that a control
# exists in the source.
_MUST_BE_EXPORTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("routing.upstream_connect_timeout_s", ("routing", "upstream_connect_timeout_s")),
    ("routing.upstream_read_timeout_s", ("routing", "upstream_read_timeout_s")),
    ("agent.max_concurrency", ("agent", "max_concurrency")),
    ("routing.model_aliases", ("routing", "model_aliases")),
)


@pytest.mark.parametrize(
    ("field", "path"), _MUST_BE_EXPORTED, ids=[f for f, _ in _MUST_BE_EXPORTED]
)
def test_a_rendered_control_is_actually_fed_by_the_summary(
    field: str, path: tuple[str, ...]
) -> None:
    """`GET /netllm/v1/config` must carry every field a surface renders."""
    from netllm_agent.admin import config_summary
    from netllm_core.models import NetllmConfig

    node: object = config_summary(NetllmConfig())
    for key in path:
        assert isinstance(node, dict) and key in node, (
            f"{field}: config_summary does not export {'.'.join(path)}. The "
            "control renders against undefined and POSTs an empty value back, "
            "which config_merge treats as an explicit erase."
        )
        node = node[key]


def test_cloud_provider_rows_carry_their_rendered_fields() -> None:
    """Same contract for the per-provider rows, which have their own export."""
    from netllm_agent.admin import config_summary
    from netllm_core.models import CloudProviderConfig, NetllmConfig

    cfg = NetllmConfig()
    cfg.cloud.providers = {
        "openai": CloudProviderConfig(
            enabled=True, api_key_env="MY_KEY", base_url="https://p.example/v1"
        )
    }
    row = config_summary(cfg)["cloud"]["providers"]["openai"]
    for key in ("api_key_env", "base_url"):
        assert key in row, (
            f"cloud.providers[].{key} is rendered on both surfaces but "
            "_cloud_provider_export does not send it; the control reads empty "
            "and POSTs '' back, erasing the stored value"
        )


def test_no_row_field_excuse_is_stale() -> None:
    """A `[[row_field]]` excuse must name a field the Swift struct really omits.

    The ledger's other sections get a staleness rule; this one had none, and
    it was consulted only for the models in `IDENTITYLESS` — so neither
    shipped entry was read by any assertion. Adversarial review found the
    predictable result: `BackendOverride.api_key` was excused as "not carried
    by the Swift struct" while `NetllmConfigDocument.swift` declares
    `var api_key: String = ""`. An excuse nothing checks is not documentation,
    it is cover.

    Models with no Swift counterpart at all (`SourceConfig` is `[JSONValue]`)
    are skipped rather than failed — there is no struct to omit a field from.
    """
    models = {
        "RoutingPolicy": RoutingPolicy,
        "BackendOverride": BackendOverride,
        "SourceConfig": SourceConfig,
    }
    stale = []
    for entry in _control_ledger().get("row_field", []):
        model_name, field = entry["model"], entry["field"]
        assert model_name in models, f"unknown model in row_field ledger: {model_name}"
        assert field in models[model_name].model_fields, (
            f"{model_name}.{field} is excused but no longer exists on the "
            "pydantic model — delete the entry"
        )
        try:
            swift_fields, location = _swift_struct_fields(model_name)
        except AssertionError:
            continue  # no typed Swift struct; nothing to omit
        if field in swift_fields:
            stale.append(f"{model_name}.{field} (declared at {location})")
    assert not stale, (
        "these row_field excuses claim the Swift struct omits a field it "
        "actually declares:\n  " + "\n  ".join(stale)
    )
