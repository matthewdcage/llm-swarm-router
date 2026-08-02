#!/usr/bin/env python3
"""Measure the as-built ``service/`` package's coupling, comparably to the plan.

``analyze-module-graph.py`` answers "if we split this one module along these
proposed seams, how much coupling crosses them?" — it takes a single file plus a
hypothetical cluster map. That is the right question *before* a split and the
wrong shape *after* one, because the seams are now real files.

This script closes the loop so the F-26 claim in
``docs/architecture/07-findings-register.md`` is reproducible rather than
asserted. It synthesises one module from the split package — every mixin/adapter
class body merged into a single ``class AgentService``, which is exactly what
mixin composition does at runtime — and derives the cluster map from the real
file each node came from. ``analyze()`` then runs **unchanged**, so the output is
directly comparable to the monolith baseline.

Reproduce the register's numbers::

    # baseline: the monolith under the adopted plan's seams
    git show a4c8893:packages/netllm-agent/src/netllm_agent/service.py > /tmp/base.py
    python3 scripts/analyze-module-graph.py --layout service-plan \
        --file /tmp/base.py --format json

    # as-built
    python3 scripts/measure-asbuilt-coupling.py

At the close of the consolidation: ``cross_cluster_call_edges`` 129 -> 33,
``shared_state_multi_writer`` 12 -> 0, ``cycles.two_cycles`` 2 -> 0.

Same limitations as the analyzer it wraps (pure AST: dynamic dispatch, getattr,
decorators and non-literal ``self`` receivers are invisible). The absolute
numbers are only meaningful against each other, measured the same way.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYZER = REPO_ROOT / "scripts" / "analyze-module-graph.py"
SERVICE_PKG = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/service"


def _load_analyzer():
    spec = importlib.util.spec_from_file_location("analyze_module_graph", ANALYZER)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot load {ANALYZER}")
    module = importlib.util.module_from_spec(spec)
    # The analyzer uses @dataclass, which resolves annotations through
    # sys.modules[cls.__module__]; register before exec or it raises.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _synthesize(pkg: Path) -> tuple[str, dict[str, str]]:
    """Merge the package into one module + the real-file cluster map."""
    files = sorted(pkg.glob("*.py")) + sorted((pkg / "surfaces").glob("*.py"))
    methods: list[ast.stmt] = []
    module_level: list[ast.stmt] = []
    clusters: dict[str, str] = {}

    for path in files:
        rel = path.relative_to(pkg).as_posix()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef):
                        methods.append(sub)
                        clusters[f"AgentService.{sub.name}"] = rel
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                module_level.append(node)
                clusters[node.name] = rel

    merged = ast.ClassDef(
        name="AgentService",
        bases=[],
        keywords=[],
        body=methods or [ast.Pass()],
        decorator_list=[],
    )
    tree = ast.Module(body=[*module_level, merged], type_ignores=[])
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), clusters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--package",
        type=Path,
        default=SERVICE_PKG,
        help="service package to measure (default: netllm_agent/service)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="emit the whole report, not just counts and cycles",
    )
    args = parser.parse_args(argv)

    analyzer = _load_analyzer()
    source, clusters = _synthesize(args.package)
    report = analyzer.analyze(analyzer.ModuleGraph(source, str(args.package)), clusters)
    if not args.full:
        report = {"counts": report["counts"], "cycles": report["cycles"]}
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
