#!/usr/bin/env python3
"""Derive tests/contract/route-auth-gates.json from the PRE-SPLIT app.py.

Phase 5b moved every route out of ``create_app`` into ``netllm_agent/routes/``.
The route *set* is guarded by ``tests/contract/routes.json``; the thing that
guards nothing by itself is which auth gate each route applies. A route
quietly losing ``require_read_access`` is F-59 again (a read gate that was a
no-op, exposing every configured cloud credential to the LAN) and no existing
test would have failed.

So the expected mapping is not written by hand and not read out of the
post-split tree — it is parsed out of ``app.py`` **as it was at the pinned
pre-split commit**, straight from git:

    uv run python scripts/generate-route-auth-gates.py            # regenerate
    uv run python scripts/generate-route-auth-gates.py --check    # CI-style

``--ref`` defaults to the ``source_commit`` already recorded in the JSON, so
regenerating cannot silently re-baseline against the refactored file: moving
the baseline means editing the pinned commit in the same diff, in the open.

Derivation is AST-based: inside ``create_app``, every nested function carrying
an ``@app.<method>("<path>")`` decorator is a route, and the gate is whichever
of the three gate names its body calls. ``/ui`` is a StaticFiles mount, not a
handler, and is excluded here (it is still covered by routes.json).
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests/contract/route-auth-gates.json"
APP_PATH = "packages/netllm-agent/src/netllm_agent/app.py"

# The commit whose app.py is the source of truth for this mapping: the last
# commit before the Phase 5b route split.
DEFAULT_REF = "12c4e7157439d2f03b29fe35906014aeaa4e3d11"

GATE_NAMES = frozenset(
    {"require_admin_access", "require_read_access", "require_inference_access"}
)
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _pinned_ref() -> str:
    if OUT.is_file():
        try:
            return str(json.loads(OUT.read_text(encoding="utf-8"))["source_commit"])
        except (json.JSONDecodeError, KeyError):
            pass
    return DEFAULT_REF


def read_source(ref: str) -> str:
    return subprocess.run(
        ["git", "show", f"{ref}:{APP_PATH}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _route_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple | None:
    """Return (METHOD, path) for an @app.<method>("<path>") decorator."""
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            continue
        if dec.func.attr not in HTTP_METHODS:
            continue
        target = dec.func.value
        if not isinstance(target, ast.Name) or target.id != "app":
            continue
        if not dec.args or not isinstance(dec.args[0], ast.Constant):
            continue
        return dec.func.attr.upper(), str(dec.args[0].value)
    return None


def _gates_called(node: ast.AST) -> list[str]:
    found: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name in GATE_NAMES and name not in found:
            found.append(name)
    return found


def collect(source: str, ref: str) -> dict[str, object]:
    tree = ast.parse(source)
    create_app = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )
    rows: list[dict[str, object]] = []
    for node in ast.walk(create_app):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        route = _route_decorator(node)
        if route is None:
            continue
        method, path = route
        gates = _gates_called(node)
        if len(gates) > 1:
            raise SystemExit(
                f"{path} calls more than one gate ({gates}); this generator "
                "encodes one gate per route — teach it the new shape."
            )
        rows.append(
            {
                "path": path,
                "method": method,
                "handler": node.name,
                "gate": gates[0] if gates else None,
            }
        )

    if not rows:
        # Refuse to emit an empty baseline. Pointing --ref at a POST-split
        # commit finds no inline routes and would otherwise write
        # `"routes": []` and exit 0 -- a manifest that vacuously "matches"
        # every possible app. It cannot ship (--check and
        # test_gate_baseline_is_pre_split both fail), but a footgun that is
        # only caught two steps downstream is still a footgun.
        raise SystemExit(
            f"generate-route-auth-gates: create_app at {ref} registers no "
            "routes inline, so there is no gate baseline to extract. Point "
            "--ref at a PRE-split commit (routes declared inside create_app)."
        )
    rows.sort(key=lambda row: (row["path"], row["method"]))
    return {
        "_comment": (
            "Generated by scripts/generate-route-auth-gates.py from "
            f"{APP_PATH} at the PRE-SPLIT commit below — do not edit by hand. "
            "Asserted against the running app by tests/test_route_auth_gates.py."
        ),
        "source_commit": ref,
        "source_path": APP_PATH,
        "routes": rows,
    }


def render(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--ref",
        default=None,
        help="git ref to derive from (default: source_commit in the JSON)",
    )
    args = parser.parse_args()

    ref = args.ref or _pinned_ref()
    rendered = render(collect(read_source(ref), ref))
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != rendered:
            print(
                "generate-route-auth-gates: "
                "tests/contract/route-auth-gates.json is stale vs "
                f"{APP_PATH}@{ref}.\n"
                "  Regenerate with:\n"
                "    uv run python scripts/generate-route-auth-gates.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: route-auth-gates.json up to date (derived from {ref[:12]})")
        return 0

    OUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} from {APP_PATH}@{ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
