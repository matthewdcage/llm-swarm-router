#!/usr/bin/env python3
"""Prove the F-26 service split was a *move*, independently of the tests.

plan-f24-f26.md §3 Phase 9 is a pure move: "vectors byte-identical; zero test
edits other than the repoints". The vector corpus is the primary proof, but
it and the unit suite share a weakness here — Phase 9 also repoints the patch
targets those very tests use. So this check answers the question from the
source text alone, with no test execution involved:

    is every function/method body in ``service/`` byte-identical to the one
    it had in the pre-split ``service.py``, with none added, dropped, or
    quietly edited?

It parses both sides with ``ast``, keys every callable by its qualified name,
and diffs the *source segments*. Anything that is not a byte-for-byte match
must be listed in ``DECLARED_SEAMS`` below with a reason — the four
dependency-graph seams Phase 9 was required to resolve, plus the three
``AgentService.``-qualified self-references that had to name their new owning
class. An undeclared difference exits non-zero.

Usage::

    python3 scripts/check-service-split-mechanical.py            # vs git HEAD
    python3 scripts/check-service-split-mechanical.py OLD.py     # vs a file
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/service"
OLD_PATH = "packages/netllm-agent/src/netllm_agent/service.py"

# Pre-split modules that the split absorbed as-is (they were already carved
# out by Phases 6-7 and only changed directory in Phase 9).
MOVED_WHOLESALE = {
    "packages/netllm-agent/src/netllm_agent/engine.py": "engine.py",
    "packages/netllm-agent/src/netllm_agent/surfaces/base.py": "surfaces/base.py",
    "packages/netllm-agent/src/netllm_agent/surfaces/chat.py": "surfaces/chat.py",
    "packages/netllm-agent/src/netllm_agent/surfaces/embeddings.py": (
        "surfaces/embeddings.py"
    ),
    "packages/netllm-agent/src/netllm_agent/surfaces/messages.py": (
        "surfaces/messages.py"
    ),
    "packages/netllm-agent/src/netllm_agent/surfaces/__init__.py": (
        "surfaces/__init__.py"
    ),
}

# name -> why it is allowed to differ. Every entry is a seam the Phase 9
# brief required, or a self-reference that had to be renamed because the
# composed class is no longer the defining class.
DECLARED_SEAMS: dict[str, str] = {
    "AgentService.apply_config": (
        "[S4] `self._local_scan_cache = None` -> `self.invalidate_local_scan_cache()`"
    ),
    "AgentService.build_candidates": (
        "[S2] the anthropic fallback tier arrives as an argument from the "
        "adapter instead of being fetched by a `plan.surface` branch, which "
        "is what removes the selection.py <-> surfaces/messages.py cycle"
    ),
    "BaseAdapter.candidates": (
        "[S2] passes `fallback_tiers=self.fallback_tiers(plan, cloud_extra)` "
        "through to build_candidates — the other half of the same seam"
    ),
    "AgentService._maybe_follow_gateway": (
        "[S3] the config write goes through core.apply_runtime_strategy"
    ),
    "AttemptRecorder.success_from_result": (
        "[M1] `AgentService._usage_from_response` -> "
        "`self._service._usage_from_response` (same staticmethod, reached "
        "through the service the recorder already holds)"
    ),
    "AgentService._wants_local_only": (
        "[M2] `AgentService._normalize_headers/_incoming_hops` -> "
        "`PolicyMixin.…`; both are staticmethods on the mixin that now "
        "defines them"
    ),
}

# Callables that are new in Phase 9 (the seam entry points themselves).
DECLARED_ADDITIONS = {
    "AgentServiceCore.apply_runtime_strategy",
    "BackendsMixin.invalidate_local_scan_cache",
    "BaseAdapter.fallback_tiers",
    "AnthropicDialectAdapter.fallback_tiers",
}


def _callables(source: str, label: str) -> dict[str, str]:
    """Qualified-name -> exact source segment, for every def in ``source``."""
    tree = ast.parse(source, filename=label)
    out: dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                seg = ast.get_source_segment(source, child, padded=False)
                out[f"{prefix}{child.name}"] = seg or ""
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return out


# Every class the composed ``AgentService`` is assembled from. They are one
# identity for comparison purposes: the point of the split is that
# ``AgentService.foo`` became ``PolicyMixin.foo`` without the body moving.
SERVICE_CLASSES = frozenset(
    {
        "AgentService",
        "AgentServiceCore",
        "AccountingMixin",
        "BackendsMixin",
        "CloudMixin",
        "PolicyMixin",
        "SelectionMixin",
        "StatusMixin",
        "SwarmTasksMixin",
        "SurfaceHelpersMixin",
        "ChatSurfaceMixin",
        "EmbeddingsSurfaceMixin",
        "MessagesSurfaceMixin",
        "ResponsesSurfaceMixin",
    }
)


def _strip_class(name: str) -> str:
    """Canonical key: the qualified name with the mixin split folded away."""
    parts = name.split(".")
    return ".".join("SERVICE" if p in SERVICE_CLASSES else p for p in parts)


def _git_show(rev_path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", rev_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        old_src = Path(argv[1]).read_text()
    else:
        old_src = _git_show(f"HEAD:{OLD_PATH}") or ""
        if not old_src:
            print(
                f"cannot read HEAD:{OLD_PATH} — pass the pre-split file as "
                "an argument (the split may already be committed)",
                file=sys.stderr,
            )
            return 2

    old: dict[str, str] = {}
    for qname, seg in _callables(old_src, OLD_PATH).items():
        old[_strip_class(qname)] = seg
    pre_tree = Path(argv[2]) if len(argv) > 2 else None
    for rel, _new_rel in MOVED_WHOLESALE.items():
        src: str | None = None
        if pre_tree is not None:
            candidate = pre_tree / rel.split("netllm_agent/", 1)[1]
            if candidate.exists():
                src = candidate.read_text()
        if src is None:
            src = _git_show(f"HEAD:{rel}")
        if src:
            for qname, seg in _callables(src, rel).items():
                old.setdefault(_strip_class(qname), seg)

    new: dict[str, str] = {}
    owner: dict[str, str] = {}
    for path in sorted(PKG.rglob("*.py")):
        for qname, seg in _callables(path.read_text(), str(path)).items():
            short = _strip_class(qname)
            if short in new:
                print(f"DUPLICATE definition of {short}: {owner[short]} and {path}")
                return 1
            new[short] = seg
            owner[short] = f"{path.relative_to(PKG)}::{qname}"

    problems: list[str] = []

    for name in sorted(set(old) - set(new)):
        problems.append(f"MISSING  {name} — defined before the split, gone after")

    for name in sorted(set(new) - set(old)):
        qual = owner[name].split("::", 1)[1]
        if qual in DECLARED_ADDITIONS or name in {
            n.rsplit(".", 1)[-1] for n in DECLARED_ADDITIONS
        }:
            continue
        problems.append(f"ADDED    {name} ({owner[name]}) — not a declared seam")

    for name in sorted(set(old) & set(new)):
        if old[name] == new[name]:
            continue
        qual = owner[name].split("::", 1)[1]
        declared = [
            reason
            for key, reason in DECLARED_SEAMS.items()
            if _strip_class(key) == name
        ]
        if declared:
            continue
        problems.append(
            f"CHANGED  {name} ({owner[name]}) — body differs and no seam is "
            "declared for it"
        )

    identical = sum(1 for n in set(old) & set(new) if old[n] == new[n])
    print(
        f"pre-split callables: {len(old)}   post-split: {len(new)}   "
        f"byte-identical: {identical}"
    )
    for key, reason in sorted(DECLARED_SEAMS.items()):
        short = _strip_class(key)
        state = (
            "differs"
            if short in old and short in new and old[short] != new[short]
            else "IDENTICAL (declaration now unnecessary)"
        )
        print(f"  declared seam {key}: {state} — {reason}")

    if problems:
        print("\n".join(problems))
        return 1
    print("OK: the split is mechanical modulo the declared seams")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
