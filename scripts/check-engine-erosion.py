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


def main() -> int:
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
