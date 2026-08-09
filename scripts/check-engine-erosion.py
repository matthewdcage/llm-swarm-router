#!/usr/bin/env python3
"""Anti-erosion gate for the one failover loop (plan-f24-f26.md §1, §3 Phase 6).

The F-24 defect was never "the loop is long". It was that there were five
loops, so a fix landed on one of them. Phase 6 collapses the non-streaming
paths into ``engine.run_with_failover``; nothing stops a future change from
adding ``if plan.surface is Surface.MESSAGES:`` back into it, one small
special case at a time, until there are five loops again wearing one name.

This check makes that regression a build failure. ``engine.py`` may not:

1. import any ``surfaces/`` module except ``base`` (the protocol) — an
   import of a *concrete* adapter is how surface knowledge gets in;
2. name ``Surface`` or any of its members;
3. read ``.surface`` off anything (``plan.surface``, ``adapter.surface``);
4. ``isinstance``-test an adapter against a concrete adapter class.

A new per-surface need extends ``SurfaceAdapter``; the loop never branches.

Run standalone (``scripts/check-engine-erosion.py``, exit 1 on failure, part
of ``scripts/ci.sh lint``) or via ``tests/contract/test_engine_erosion.py``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/service/engine.py"

# The Surface enum's members. Kept as literals on purpose: importing the enum
# here would make the gate depend on the code it polices.
SURFACE_MEMBERS = ("CHAT", "EMBEDDINGS", "MESSAGES", "RESPONSES")
ALLOWED_SURFACE_MODULE = "surfaces.base"


def _module_path(node: ast.ImportFrom | ast.Import) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return [node.module or ""]


def _normalize(module: str) -> str:
    """Strip the package prefix so relative and absolute forms compare equal.

    [Phase 9] ``surfaces`` moved under ``netllm_agent.service``, so both
    package prefixes have to come off — otherwise
    ``from netllm_agent.service.surfaces import adapter_for`` normalizes to
    ``service.surfaces`` and slips past a gate that only knows ``surfaces``.
    """
    return (
        module.removeprefix("netllm_agent.")
        .removeprefix("service.")
        .lstrip(".")
        .removeprefix("service.")
    )


def check(path: Path = ENGINE) -> list[str]:
    """Return a list of violations; empty means the gate passes."""
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    rel = (
        path.relative_to(REPO_ROOT).as_posix()
        if path.is_relative_to(REPO_ROOT)
        else path.as_posix()
    )
    violations: list[str] = []

    def report(node: ast.AST, message: str) -> None:
        violations.append(f"{rel}:{getattr(node, 'lineno', '?')}: {message}")

    for node in ast.walk(tree):
        # 1. surfaces/* imports
        if isinstance(node, ast.Import | ast.ImportFrom):
            for raw in _module_path(node):
                module = _normalize(raw)
                if not module.startswith("surfaces"):
                    continue
                if module != ALLOWED_SURFACE_MODULE:
                    report(
                        node,
                        f"imports {raw!r}; the engine may import only "
                        f"{ALLOWED_SURFACE_MODULE!r} (the SurfaceAdapter "
                        "protocol). Extend the protocol instead.",
                    )
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name.endswith("Adapter") and alias.name not in (
                            "SurfaceAdapter",
                            "BaseAdapter",
                        ):
                            report(
                                node,
                                f"imports the concrete adapter {alias.name!r}; "
                                "the engine sees only the protocol.",
                            )

        # 2. the Surface enum, by name or by member
        if isinstance(node, ast.Name) and node.id == "Surface":
            report(node, "names the Surface enum; the loop is surface-agnostic.")
        if isinstance(node, ast.Attribute) and node.attr in SURFACE_MEMBERS:
            if isinstance(node.value, ast.Name) and node.value.id == "Surface":
                report(
                    node,
                    f"references Surface.{node.attr}; that decision belongs "
                    "to a SurfaceAdapter member.",
                )

        # 3. reading .surface off anything
        if isinstance(node, ast.Attribute) and node.attr == "surface":
            report(
                node,
                "reads `.surface`; branching on it is the erosion this gate "
                "exists to stop.",
            )

        # 4. isinstance(adapter, SomeAdapter)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            probed = ast.unparse(node.args[1])
            if "Adapter" in probed:
                report(
                    node,
                    f"isinstance-tests the adapter against {probed}; "
                    "dispatch through the protocol instead.",
                )

    return violations


SEAM_LEDGER = REPO_ROOT / "tests/conformance/ledgers/surface-seams.toml"
AGENT_SRC = REPO_ROOT / "packages/netllm-agent/src/netllm_agent"
ADAPTER_PACKAGE = AGENT_SRC / "service/surfaces"


def _surface_branches() -> list[tuple[str, int, str]]:
    """Every `Surface`-keyed conditional outside the adapter package.

    Axis C's rule: a surface branch belongs on the outside of the failover
    loop, in `service/surfaces/`. One anywhere else is a place a new surface
    can be silently forgotten -- nothing enumerates them, so the omission is
    a runtime behaviour difference rather than a failed build.

    Comments and docstrings are skipped: prose describing a branch that was
    REMOVED still names it, and counting that would make the gate fire on its
    own changelog. (Learned the hard way in Phase 4.)
    """
    found: list[tuple[str, int, str]] = []
    for path in sorted(AGENT_SRC.rglob("*.py")):
        if ADAPTER_PACKAGE in path.parents or path == ENGINE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Compare, ast.IfExp, ast.If)):
                continue
            segment = ast.dump(node)
            if "attr='" not in segment:
                continue
            for member in SURFACE_MEMBERS:
                if f"attr='{member}'" in segment and "id='Surface'" in segment:
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    found.append((rel, node.lineno, member))
                    break
    return sorted(set(found))


def check_seams() -> int:
    import tomllib

    branches = _surface_branches()
    if not SEAM_LEDGER.exists():
        print(f"error: {SEAM_LEDGER} not found", file=sys.stderr)
        return 1
    ledger = tomllib.loads(SEAM_LEDGER.read_text(encoding="utf-8"))
    allowed = {(entry["file"], entry["member"]) for entry in ledger.get("seam", [])}
    unledgered = [
        f"{rel}:{lineno}: Surface.{member} branch outside service/surfaces/"
        for rel, lineno, member in branches
        if (rel, member) not in allowed
    ]
    if unledgered:
        print("surface-seams gate FAILED:", file=sys.stderr)
        for line in unledgered:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nDeclare the per-surface fact on taxonomy.SurfaceSpec (or as a "
            "SurfaceAdapter member) so every surface has to answer it, or "
            f"ledger it in {SEAM_LEDGER.relative_to(REPO_ROOT)} with a reason "
            "and an expiry.",
            file=sys.stderr,
        )
        return 1
    stale = [
        f"{file}:{member}"
        for (file, member) in allowed
        if not any(r == file and m == member for r, _, m in branches)
    ]
    if stale:
        print(
            "surface-seams gate FAILED: ledgered seams that no longer exist "
            "(delete the entries):\n  " + "\n  ".join(stale),
            file=sys.stderr,
        )
        return 1
    print(f"OK: surface seams — {len(branches)} ledgered, 0 unledgered")
    return 0


def main() -> int:
    if "--seams" in sys.argv:
        return check_seams()
    if not ENGINE.exists():
        print(f"error: {ENGINE} not found", file=sys.stderr)
        return 1
    violations = check()
    if violations:
        print("engine anti-erosion gate FAILED:", file=sys.stderr)
        for line in violations:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThe failover loop must stay surface-agnostic. Add the per-surface "
            "behavior to SurfaceAdapter (surfaces/base.py) and implement it in "
            "the adapter that needs it.",
            file=sys.stderr,
        )
        return 1
    print("OK: engine.py is surface-agnostic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
