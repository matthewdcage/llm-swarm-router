"""Divergence-annotation lint (plan-f24-f26.md §2, the universal gate rule).

Mechanical commits change zero vectors. Semantic commits may change only
vectors annotated with divergence IDs (D1-D16, behavior-matrix.md) that the
commit declares in tests/contract/allowed-divergences.txt. Any vector diff
vs git HEAD without a matching declared ID fails here — a machine check,
not reviewer judgment.

Brand-new vector files (absent from HEAD) are baseline recordings and pass
unconditionally unless their stable ``id`` already existed at HEAD under
another path (F-56 rename detection). Deleting a vector that HEAD has
always fails unless the same ``id`` reappears elsewhere (rename).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = Path(__file__).resolve().parent
VECTORS_DIR = CONTRACT_DIR / "vectors"
ALLOWED_FILE = CONTRACT_DIR / "allowed-divergences.txt"

_VALID_IDS = {f"D{i}" for i in range(1, 41)}

# Declaring this instead of a divergence ID asserts a harness-only re-record:
# the canonicalizer's volatile-field schema changed, so every recorded value
# that differs vs HEAD must be a volatile placeholder on both sides. The lint
# proves that claim below rather than taking it on trust.
_HARNESS_RERECORD = "HARNESS-RERECORD"

# Every token the volatile-field schema has ever emitted (canonical.py). Two
# values are interchangeable only if BOTH sides are drawn from this set.
_VOLATILE_TOKENS = {
    "<ts>",
    "<id>",
    "<fp>",
    "<latency>",
    "<ema>",
    ">0",
    "updated",
    "unchanged",
}


# Metric keys the volatile-field schema may add or remove wholesale. Only
# these may appear on one side and not the other; every other key mismatch is
# a real change.
_VOLATILE_KEY_PREFIXES = ("netllm_request_latency_seconds_sum",)


def _drop_volatile_keys(d: dict) -> dict:
    return {
        k: v
        for k, v in d.items()
        if not (isinstance(k, str) and k.startswith(_VOLATILE_KEY_PREFIXES))
    }


def _differs_beyond_volatile(head: object, cur: object) -> bool:
    """True if head/cur differ anywhere outside the volatile vocabulary."""
    if isinstance(head, dict) and isinstance(cur, dict):
        head, cur = _drop_volatile_keys(head), _drop_volatile_keys(cur)
        if head.keys() != cur.keys():
            return True
        return any(_differs_beyond_volatile(head[k], cur[k]) for k in head)
    if isinstance(head, list) and isinstance(cur, list):
        if len(head) != len(cur):
            return True
        return any(_differs_beyond_volatile(h, c) for h, c in zip(head, cur))
    if head == cur:
        return False

    # A volatile leaf may move between tokens (0 was a legacy latency-sum
    # rendering), but never to or from a real recorded value. Only str/int
    # leaves can be volatile: a dict or list operand here is a real change,
    # and `x in _VOLATILE_TOKENS` on a dict would raise TypeError.
    def _volatile_leaf(value: object) -> bool:
        if isinstance(value, str):
            return value in _VOLATILE_TOKENS
        # bool is an int subclass; False must never count as the legacy 0.
        return isinstance(value, int) and not isinstance(value, bool) and value == 0

    return not (_volatile_leaf(head) and _volatile_leaf(cur))


def _git_show(rel_path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _head_vector_names() -> set[str]:
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "tests/contract/vectors/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return set()
    # Keyed on the repo-relative path, not the basename: vectors live in
    # per-group subdirectories and basenames repeat across them.
    return {line for line in proc.stdout.splitlines() if line.endswith(".json")}


def _head_id_to_path() -> dict[str, str]:
    """Stable vector ``id`` -> repo-relative path at HEAD (F-56)."""
    mapping: dict[str, str] = {}
    for rel_path in _head_vector_names():
        head_bytes = _git_show(rel_path)
        if head_bytes is None:
            continue
        doc = json.loads(head_bytes)
        vid = doc.get("id")
        if isinstance(vid, str) and vid:
            mapping[vid] = rel_path
    return mapping


def _rename_head_path(
    doc: dict[str, object], cur_path: str, head_id_to_path: dict[str, str]
) -> str | None:
    """If ``doc``'s id lived at HEAD under another path, return that path."""
    vid = doc.get("id")
    if not isinstance(vid, str) or not vid:
        return None
    old_path = head_id_to_path.get(vid)
    if old_path and old_path != cur_path:
        return old_path
    return None


def _vector_change_violation(
    name: str,
    head_doc: dict[str, object],
    cur_doc: dict[str, object],
    *,
    allowed: set[str],
    harness_rerecord: bool,
) -> str | None:
    """Return a violation message, or None if the change is allowed."""
    if harness_rerecord and not _differs_beyond_volatile(head_doc, cur_doc):
        return None
    ids = set(cur_doc.get("divergence", []))
    if not ids:
        return f"{name}: changed vs HEAD but carries no divergence IDs"
    if not ids <= allowed:
        return (
            f"{name}: changed vs HEAD with IDs {sorted(ids)} but "
            f"allowed-divergences.txt declares only {sorted(allowed)}"
        )
    return None


def load_allowed_divergences() -> set[str]:
    allowed: set[str] = set()
    for raw in ALLOWED_FILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    return allowed


def test_allowed_divergences_file_is_valid() -> None:
    allowed = load_allowed_divergences() - {_HARNESS_RERECORD}
    unknown = allowed - _VALID_IDS
    assert not unknown, f"unknown divergence IDs in allowed-divergences.txt: {unknown}"


def test_vector_divergence_annotations_are_valid_ids() -> None:
    for path in sorted(VECTORS_DIR.rglob("*.json")):
        doc = json.loads(path.read_text())
        ids = set(doc.get("divergence", []))
        unknown = ids - _VALID_IDS
        assert not unknown, f"{path.name}: unknown divergence IDs {unknown}"


def test_changed_vectors_carry_declared_divergence_ids() -> None:
    allowed = load_allowed_divergences()
    harness_rerecord = _HARNESS_RERECORD in allowed
    allowed = allowed - {_HARNESS_RERECORD}
    working = {
        p.relative_to(REPO_ROOT).as_posix(): p for p in VECTORS_DIR.rglob("*.json")
    }

    head_id_to_path = _head_id_to_path()
    working_id_to_path: dict[str, str] = {}
    for name, path in working.items():
        vid = json.loads(path.read_text()).get("id")
        if isinstance(vid, str) and vid:
            working_id_to_path[vid] = name

    deleted = _head_vector_names() - set(working)
    silent_deletes: list[str] = []
    for rel in sorted(deleted):
        head_bytes = _git_show(rel)
        if head_bytes is None:
            silent_deletes.append(rel)
            continue
        vid = json.loads(head_bytes).get("id")
        if (
            isinstance(vid, str)
            and vid
            and working_id_to_path.get(vid) not in (None, rel)
        ):
            continue  # same id at a new path — rename, not a drop
        silent_deletes.append(rel)
    assert not silent_deletes, (
        f"vectors deleted vs HEAD: {silent_deletes} — a checked-in contract "
        "vector must never be silently dropped"
    )

    violations: list[str] = []
    for name, path in sorted(working.items()):
        doc = json.loads(path.read_text())
        head_bytes = _git_show(name)
        if head_bytes is None:
            rename_from = _rename_head_path(doc, name, head_id_to_path)
            if rename_from is None:
                continue  # new id: baseline recording
            head_bytes = _git_show(rename_from)
            if head_bytes is None:
                continue
        elif head_bytes == path.read_bytes():
            continue  # unchanged at same path
        head_doc = json.loads(head_bytes)
        msg = _vector_change_violation(
            name,
            head_doc,
            doc,
            allowed=allowed,
            harness_rerecord=harness_rerecord,
        )
        if msg:
            violations.append(msg)
    assert not violations, "\n".join(violations)


def test_rename_with_dropped_divergence_is_not_baseline() -> None:
    """F-56: a path rename must not launder content or annotation changes."""
    head_doc = {
        "id": "example-vector",
        "divergence": ["D7"],
        "expected": {"status": 502},
    }
    cur_doc = {
        "id": "example-vector",
        "divergence": [],
        "expected": {"status": 404},
    }
    msg = _vector_change_violation(
        "tests/contract/vectors/group/new-name.json",
        head_doc,
        cur_doc,
        allowed=set(),
        harness_rerecord=False,
    )
    assert msg is not None
    assert "no divergence IDs" in msg


def test_rename_with_harness_only_volatile_diff_is_allowed() -> None:
    head_doc = {"id": "v", "divergence": [], "expected": {"latency": "<latency>"}}
    cur_doc = {"id": "v", "divergence": [], "expected": {"latency": 0}}
    msg = _vector_change_violation(
        "new.json",
        head_doc,
        cur_doc,
        allowed=set(),
        harness_rerecord=True,
    )
    assert msg is None
