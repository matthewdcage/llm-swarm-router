"""Divergence-annotation lint (plan-f24-f26.md §2, the universal gate rule).

Mechanical commits change zero vectors. Semantic commits may change only
vectors annotated with divergence IDs (D1-D15, behavior-matrix.md) that the
commit declares in tests/contract/allowed-divergences.txt. Any vector diff
vs git HEAD without a matching declared ID fails here — a machine check,
not reviewer judgment.

Brand-new vector files (absent from HEAD) are baseline recordings and pass
unconditionally; deleting a vector that HEAD has always fails.
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


def _differs_beyond_volatile(head: object, cur: object) -> bool:
    """True if head/cur differ anywhere outside the volatile vocabulary."""
    if isinstance(head, dict) and isinstance(cur, dict):
        if head.keys() != cur.keys():
            return True
        return any(_differs_beyond_volatile(head[k], cur[k]) for k in head)
    if isinstance(head, list) and isinstance(cur, list):
        if len(head) != len(cur):
            return True
        return any(_differs_beyond_volatile(h, c) for h, c in zip(head, cur))
    if head == cur:
        return False
    # A volatile leaf may move between tokens (and 0 was a legacy latency-sum
    # rendering), but never to or from a real recorded value.
    return not (
        (head in _VOLATILE_TOKENS or head == 0)
        and (cur in _VOLATILE_TOKENS or cur == 0)
    )


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

    deleted = _head_vector_names() - set(working)
    assert not deleted, (
        f"vectors deleted vs HEAD: {sorted(deleted)} — a checked-in contract "
        "vector must never be silently dropped"
    )

    violations: list[str] = []
    for name, path in sorted(working.items()):
        head_bytes = _git_show(name)
        if head_bytes is None:
            continue  # new file: baseline recording, always allowed
        if head_bytes == path.read_bytes():
            continue  # unchanged
        doc = json.loads(path.read_text())
        if harness_rerecord:
            if _differs_beyond_volatile(json.loads(head_bytes), doc):
                violations.append(
                    f"{name}: declared {_HARNESS_RERECORD} but differs vs HEAD "
                    "outside the volatile-field vocabulary"
                )
            continue
        ids = set(doc.get("divergence", []))
        if not ids:
            violations.append(f"{name}: changed vs HEAD but carries no divergence IDs")
        elif not ids <= allowed:
            violations.append(
                f"{name}: changed vs HEAD with IDs {sorted(ids)} but "
                f"allowed-divergences.txt declares only {sorted(allowed)}"
            )
    assert not violations, "\n".join(violations)
