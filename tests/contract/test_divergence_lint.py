"""Divergence-annotation lint (plan-f24-f26.md §2, the universal gate rule).

Mechanical commits change zero vectors. Semantic commits may change only
vectors annotated with divergence IDs (D1-D16, behavior-matrix.md) that the
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


def _head_vectors_by_id() -> dict[str, tuple[str, dict]]:
    """Every HEAD vector keyed on its stable ``id`` field.

    [F-56] The lint used to key only on path, so a rename presented as a
    brand-new file ("always allowed") and BOTH content changes and
    divergence-annotation removals made in the same commit slipped through
    unchecked. Every vector carries a unique ``id``, so a renamed vector can
    be matched back to its HEAD self and held to the same comparison as an
    in-place edit.
    """
    out: dict[str, tuple[str, dict]] = {}
    for rel in _head_vector_names():
        raw = _git_show(rel)
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        vid = doc.get("id")
        if isinstance(vid, str) and vid:
            out[vid] = (rel, doc)
    return out


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

    head_by_id = _head_vectors_by_id()
    working_ids = {
        doc_id: p
        for p in working.values()
        if isinstance(doc_id := json.loads(p.read_text()).get("id"), str)
    }

    # [F-56] A path that vanished is only a deletion if its vector id also
    # vanished; if the id resurfaced elsewhere the vector was renamed, and it
    # is checked below exactly like an in-place edit.
    deleted = {
        rel
        for rel in _head_vector_names() - set(working)
        if not any(rel == head_by_id.get(vid, ("", {}))[0] for vid in working_ids)
    }
    assert not deleted, (
        f"vectors deleted vs HEAD: {sorted(deleted)} — a checked-in contract "
        "vector must never be silently dropped"
    )

    violations: list[str] = []
    for name, path in sorted(working.items()):
        head_bytes = _git_show(name)
        if head_bytes is None:
            # [F-56] Not automatically a new baseline: if this vector's id
            # exists at another path in HEAD it is a rename, and its content
            # and annotations must still justify themselves.
            doc_id = json.loads(path.read_text()).get("id")
            renamed_from = head_by_id.get(doc_id) if isinstance(doc_id, str) else None
            if renamed_from is None:
                continue  # genuinely new: baseline recording, always allowed
            head_bytes = json.dumps(renamed_from[1], indent=2).encode()
            if json.loads(path.read_text()) == renamed_from[1]:
                continue  # pure rename, content and annotations untouched
        if head_bytes == path.read_bytes():
            continue  # unchanged
        doc = json.loads(path.read_text())
        # A harness re-record and a semantic phase can land in the same PR, so
        # the two channels compose per vector: a vector whose diff is entirely
        # volatile is covered by the declaration, and anything else still has
        # to justify itself with divergence IDs below.
        if harness_rerecord and not _differs_beyond_volatile(
            json.loads(head_bytes), doc
        ):
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
