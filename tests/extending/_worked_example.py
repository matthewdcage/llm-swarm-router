"""Shared machinery for the Phase 8 worked-example tests.

A *worked example* injects one fixture entry into a live registry and asserts
it reaches every downstream surface. The claim it discharges is **not** the
one `docs/extending/PROGRAM.md` §8 wrote:

    "with zero source edits beyond the registry entry"

That claim was measured against this tree and is false. Adding a fifth local
provider leaves labels, ports, platform gating, offline hints, the discovery
roster, the config schema document and the served projection all correct with
no edit at all -- and then fails on `ProviderId`, a hand-written `Literal` in
`models.py`, because a derived `Literal` blinds basedpyright (PROGRAM.md §6.2
refuses to open it, on purpose). `CloudProviderId` is the same shape.

The claim these tests actually assert is:

    zero source edits beyond the registry entry **and its declared
    hand-written companions**, where every companion is enumerated with the
    reason it is hand-written.

That is only worth having if the enumeration cannot quietly grow, so each
axis asserts three properties, not one:

1. **Sufficiency** -- entry + declared companions, and nothing else, makes
   every stage pass. A *sixth* hand-edit becoming necessary fails the stage
   that needs it, by name.
2. **Necessity** -- omit any one companion and at least one stage must fail.
   A companion that stops being required fails here, so the list cannot
   accumulate dead entries that make the guarantee look worse than it is.
3. **Classification** -- every mirror the ledger allows for this fact class
   is classified as a hand-written companion, a generated block, or a
   declared capability branch. A new ledger row fails until someone says
   which it is, so the enumeration cannot grow behind the tests' back.

Nothing here writes to the working tree. File-shaped companions and the
generation rail are exercised against a temporary copy of the real files, so
"the contributor made the edit" and "the contributor ran the generator" are
both really performed and really parsed, rather than asserted about.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import shutil
import sys
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
MIRROR_LEDGER = REPO_ROOT / "tests/conformance/ledgers/mirrors.toml"
GENERATOR = REPO_ROOT / "scripts/generate-registry-artifacts.py"

#: Every file `scripts/generate-registry-artifacts.py` reads or writes. The
#: temp tree has to carry all of them: the generator rewrites all five blocks
#: on every run, so a missing input is a crash rather than a skipped block.
GENERATOR_TREE_FILES = (
    "packages/netllm-core/src/netllm_core/cloud_providers.py",
    "packages/netllm-core/src/netllm_core/local_providers.py",
    "packages/netllm-core/src/netllm_core/control_plane.py",
    "tests/conformance/ledgers/control-parity.toml",
    "packages/netllm-agent/src/netllm_agent/static/dashboard.js",
    "apps/netllm-mac/Sources/Config/KeychainStore.swift",
    "config.example.toml",
    "docs/extending/08-control-parity.md",
)

SWIFT_SETTINGS = "apps/netllm-mac/Sources/AppView/SettingsViewModel.swift"


# --- companion model ------------------------------------------------------


#: How a companion's absence is caught. This is the honest part: the two
#: axes' `Literal` companions are *not* enforced the same way, and a guide
#: that said "the test catches it" for both would be wrong about one.
#:
#: ``runtime``      pydantic rejects the value at parse time; ordinary code
#:                  raises without the edit.
#: ``projection``   a conformance-kit test parses the surface file and fails
#:                  naming the missing id.
#: ``static-only``  nothing at runtime and no pytest failure: only
#:                  basedpyright and editor completion see it. The kit's
#:                  ``get_args`` equality assertion is what makes it visible
#:                  to CI at all.
Enforcement = str


@dataclass(frozen=True)
class Companion:
    """One hand-written edit that must accompany a registry entry."""

    name: str
    """Symbol a contributor searches for, e.g. `ProviderId`."""

    path: str
    """Repo-relative file holding it."""

    reason: str
    """Why it is hand-written rather than derived or generated. A companion
    with no reason is a defect, not a contract."""

    enforcement: Enforcement

    guard: str
    """The named test that fails when this companion is missing."""

    apply: Callable[[Workspace], None] | None = None
    """Performs the edit. In-process for Python symbols; a text edit in the
    temp tree for surface files. `None` is not allowed in practice -- it
    exists so a companion can be declared before it is applicable."""


@dataclass(frozen=True)
class GeneratedBlock:
    """A surface a new entry reaches by running the generator, not by hand."""

    name: str
    path: str
    command: str = "python3 scripts/generate-registry-artifacts.py"


@dataclass(frozen=True)
class CapabilityBranch:
    """A provider id literal that is a real capability check, not a roster."""

    name: str
    path: str
    reason: str


# --- the temp workspace ---------------------------------------------------


@dataclass
class Workspace:
    """A throwaway copy of the files a registry entry has to reach.

    Built lazily: most stages are pure Python and never touch it.
    """

    tmp_root: Path
    registry_edits: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Repo-relative registry file -> (dict name, source of the entry)."""

    text_edits: list[tuple[str, str, str]] = field(default_factory=list)
    """(path, anchor marker, line) inserted before the array terminator."""

    substitutions: list[tuple[str, str, str]] = field(default_factory=list)
    """(path, exact old text, new text) replaced once.

    `text_edits` only reaches multi-line arrays terminated by `\\n    ]`. A
    companion that lives inside a *single-line* Swift array -- as
    `SettingsViewModel.providers` does -- has no such terminator, so it is
    edited by exact substitution instead. The substring must occur exactly
    once; anything else is an ambiguous edit and raises.
    """

    _tree: Path | None = None

    def tree(self) -> Path:
        """Materialize the copy and run the generator against it once."""
        if self._tree is not None:
            return self._tree
        root = self.tmp_root / "tree"
        for rel in (*GENERATOR_TREE_FILES, SWIFT_SETTINGS):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / rel, dest)
        for rel, (dict_name, entry_source) in self.registry_edits.items():
            dest = root / rel
            dest.write_text(
                _splice_registry_entry(
                    dest.read_text(encoding="utf-8"), dict_name, entry_source
                ),
                encoding="utf-8",
            )
        for rel, marker, line in self.text_edits:
            dest = root / rel
            dest.write_text(
                _insert_before_array_end(
                    dest.read_text(encoding="utf-8"), marker, line
                ),
                encoding="utf-8",
            )
        for rel, old, new in self.substitutions:
            dest = root / rel
            source = dest.read_text(encoding="utf-8")
            count = source.count(old)
            assert count == 1, (
                f"substitution anchor {old!r} occurs {count} times in {rel}; "
                "it must occur exactly once"
            )
            dest.write_text(source.replace(old, new), encoding="utf-8")
        _run_generator(root)
        self._tree = root
        return root

    def read(self, rel: str) -> str:
        return (self.tree() / rel).read_text(encoding="utf-8")


def _splice_registry_entry(source: str, dict_name: str, entry_lines: str) -> str:
    """Insert `entry_lines` just before the closing brace of `dict_name`.

    Located by AST rather than by a brace count, so a nested dict inside a
    spec (`endpoints={...}`) cannot be mistaken for the registry's own
    terminator -- which is exactly the shape `CLOUD_PROVIDERS` has.
    """
    tree = ast.parse(source)
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target, value = first.id, node.value
        if target != dict_name or not isinstance(value, ast.Dict):
            continue
        assert value.end_lineno is not None
        lines = source.splitlines(keepends=True)
        at = value.end_lineno - 1  # 0-based index of the line holding `}`
        return "".join(lines[:at]) + entry_lines + "".join(lines[at:])
    raise AssertionError(f"no dict named {dict_name} to splice into")


def _insert_before_array_end(source: str, marker: str, line: str) -> str:
    """Insert `line` before the `    ]` that terminates the array at `marker`."""
    start = source.find(marker)
    assert start != -1, f"marker not found: {marker!r}"
    end = source.find("\n    ]", start)
    assert end != -1, f"unterminated array after {marker!r}"
    return source[: end + 1] + line + source[end + 1 :]


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_gen_registry_artifacts", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: the script uses `from __future__ import
    # annotations`, so `@dataclass` resolves its field types by looking its
    # own module up in `sys.modules`. Loading it unregistered raises inside
    # `dataclasses`, several frames from anything that names this file.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_generator(root: Path) -> None:
    """Rewrite every generated block in `root` from the registries in `root`.

    This is the proof that the generation rail needs no edit of its own: the
    generator is the checked-in script, unmodified, pointed at a tree whose
    registry has one more entry.
    """
    module = _load_generator()
    module.ROOT = root
    module.CLOUD_REGISTRY = (
        root / "packages/netllm-core/src/netllm_core/cloud_providers.py"
    )
    module.LOCAL_REGISTRY = (
        root / "packages/netllm-core/src/netllm_core/local_providers.py"
    )
    module.CONTROL_REGISTRY = (
        root / "packages/netllm-core/src/netllm_core/control_plane.py"
    )
    module.CONTROL_LEDGER = root / "tests/conformance/ledgers/control-parity.toml"
    argv = sys.argv
    sys.argv = ["generate-registry-artifacts.py"]
    try:
        assert module.main() == 0
    finally:
        sys.argv = argv


# --- in-process registry injection ----------------------------------------


@contextmanager
def injected(
    registry: dict, key: str, value: object, reload: tuple[str, ...]
) -> Iterator[None]:
    """Add one entry to a live registry dict and re-derive its consumers.

    `reload` names the modules that materialize a constant from the registry
    at import time (`local.py`'s `KNOWN_PROVIDERS`, `ui.py`'s
    `_PROVIDER_LABELS`). Reloading them is test plumbing that stands in for a
    fresh process -- it is *not* a source edit, and the fact that a reload is
    all it takes is precisely what proves those constants are derived. A
    companion, by contrast, is a change to the source text that no restart
    can produce.
    """
    assert key not in registry, f"{key!r} is already a real entry"
    registry[key] = value
    modules = [importlib.import_module(name) for name in reload]
    for module in modules:
        importlib.reload(module)
    try:
        yield
    finally:
        registry.pop(key, None)
        for module in modules:
            importlib.reload(module)


# --- the ledger, read as a classification obligation ----------------------


def ledger_fact_class(fact_class: str) -> dict:
    ledger = tomllib.loads(MIRROR_LEDGER.read_text(encoding="utf-8"))
    for entry in ledger["fact_class"]:
        if entry["id"] == fact_class:
            return entry
    raise AssertionError(f"mirrors.toml has no fact class {fact_class!r}")


def ledger_mirror_globs(fact_class: str) -> set[str]:
    """Files `mirrors.toml` allows to restate ids of `fact_class`."""
    entry = ledger_fact_class(fact_class)
    return {row["glob"] for row in entry.get("allowed_mirrors", [])}


def assert_classification_is_exhaustive(
    fact_class: str,
    companions: tuple[Companion, ...],
    generated: tuple[GeneratedBlock, ...],
    capability: tuple[CapabilityBranch, ...],
    guide: str,
) -> None:
    """Every ledgered mirror must be classified, and every class must be real.

    This is the property that makes the companion list *exhaustive* rather
    than merely correct today. A new place that restates a provider id can
    only land by being added to `mirrors.toml` (`check-registry-mirrors.py`
    blocks it otherwise, in `run_lint`), and the moment it is added this
    assertion fails until the worked example and its guide say which kind of
    thing it is.
    """
    ledgered = ledger_mirror_globs(fact_class)
    classified = {
        **{c.path: f"hand-written companion {c.name!r}" for c in companions},
        **{g.path: f"generated block {g.name!r}" for g in generated},
        **{c.path: f"capability branch {c.name!r}" for c in capability},
    }
    unclassified = ledgered - set(classified)
    assert not unclassified, (
        f"mirrors.toml allows {sorted(unclassified)} to restate {fact_class} ids, "
        "and the worked example does not say what they are. Classify each one as "
        "a hand-written COMPANION (with the reason it cannot be derived), a "
        f"GENERATED block, or a CAPABILITY branch, and document it in {guide}."
    )
    # The registry's own module is not a mirror of itself. A hand-written
    # companion may legitimately live beside the registry it annotates --
    # `CloudProviderId` does -- so it is exempt from the staleness half.
    source = ledger_fact_class(fact_class)["source"]
    stale = set(classified) - ledgered - {source}
    assert not stale, (
        f"the worked example classifies {sorted(stale)} but mirrors.toml no "
        f"longer allows them to restate {fact_class} ids -- the mirror is gone, "
        f"so delete the classification and the matching section of {guide}."
    )
