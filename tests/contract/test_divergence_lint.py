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

_VALID_IDS = {f"D{i}" for i in range(1, 16)}


def _git_show(rel_path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _head_vector_names() -> set[str]:
    proc = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD", "tests/contract/vectors/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return set()
    return {
        Path(line).name for line in proc.stdout.splitlines() if line.endswith(".json")
    }


def load_allowed_divergences() -> set[str]:
    allowed: set[str] = set()
    for raw in ALLOWED_FILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    return allowed


def test_allowed_divergences_file_is_valid() -> None:
    allowed = load_allowed_divergences()
    unknown = allowed - _VALID_IDS
    assert not unknown, f"unknown divergence IDs in allowed-divergences.txt: {unknown}"


def test_vector_divergence_annotations_are_valid_ids() -> None:
    for path in sorted(VECTORS_DIR.glob("*.json")):
        doc = json.loads(path.read_text())
        ids = set(doc.get("divergence", []))
        unknown = ids - _VALID_IDS
        assert not unknown, f"{path.name}: unknown divergence IDs {unknown}"


def test_changed_vectors_carry_declared_divergence_ids() -> None:
    allowed = load_allowed_divergences()
    working = {p.name: p for p in VECTORS_DIR.glob("*.json")}

    deleted = _head_vector_names() - set(working)
    assert not deleted, (
        f"vectors deleted vs HEAD: {sorted(deleted)} — a checked-in contract "
        "vector must never be silently dropped"
    )

    violations: list[str] = []
    for name, path in sorted(working.items()):
        rel = path.relative_to(REPO_ROOT).as_posix()
        head_bytes = _git_show(rel)
        if head_bytes is None:
            continue  # new file: baseline recording, always allowed
        if head_bytes == path.read_bytes():
            continue  # unchanged
        doc = json.loads(path.read_text())
        ids = set(doc.get("divergence", []))
        if not ids:
            violations.append(f"{name}: changed vs HEAD but carries no divergence IDs")
        elif not ids <= allowed:
            violations.append(
                f"{name}: changed vs HEAD with IDs {sorted(ids)} but "
                f"allowed-divergences.txt declares only {sorted(allowed)}"
            )
    assert not violations, "\n".join(violations)
