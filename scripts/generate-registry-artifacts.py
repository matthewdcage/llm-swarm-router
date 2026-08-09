#!/usr/bin/env python3
"""Generate the registry rosters that clients cannot import (PROGRAM.md §1).

Four surfaces need a provider roster and none of them can `import
netllm_core`: the dashboard's JavaScript, two Swift bootstrap lists, and the
example TOML. Each kept a hand-written copy, and the copies drifted — that is
Axis A/B's whole cost story.

The middle rung of PROGRAM.md's ladder (derive > **generate with --check** >
projection-test > mirror) applies: the block between the markers is written
from the registry, and `--check` in `run_lint` fails when it is stale. The
lists stay real literals in the file, so the dashboard still renders offline
and the Swift app still compiles with no network.

    python3 scripts/generate-registry-artifacts.py           # rewrite
    python3 scripts/generate-registry-artifacts.py --check   # exit 1 if stale

No imports of the workspace packages: this runs under bare python3 in lint,
alongside the other gates, so the registries are read by AST exactly as
check-registry-mirrors.py reads them.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLOUD_REGISTRY = ROOT / "packages/netllm-core/src/netllm_core/cloud_providers.py"
LOCAL_REGISTRY = ROOT / "packages/netllm-core/src/netllm_core/local_providers.py"
DEPRECATIONS_TOML = ROOT / "docs/deprecations.toml"
DEPRECATIONS_PY = ROOT / "packages/netllm-core/src/netllm_core/deprecations.py"
CONTROL_REGISTRY = ROOT / "packages/netllm-core/src/netllm_core/control_plane.py"
CONTROL_LEDGER = ROOT / "tests/conformance/ledgers/control-parity.toml"

BEGIN = "netllm:generated:begin"
END = "netllm:generated:end"


def _dict_keys(source: Path, name: str) -> list[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target, value = first.id, node.value
        if target != name or not isinstance(value, ast.Dict):
            continue
        return [
            key.value
            for key in value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        ]
    raise SystemExit(f"generate-registry-artifacts: {source}: no dict named {name}")


def _control_descriptors(source: Path) -> list[dict]:
    """Every `ControlDescriptor(...)` call, as plain keyword constants.

    AST rather than an import, for the same reason the provider registries are
    read this way: this script runs under bare python3 in `run_lint`, beside
    the other gates, with no workspace packages installed.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    out: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "ControlDescriptor"):
            continue
        entry: dict = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            try:
                entry[keyword.arg] = ast.literal_eval(keyword.value)
            except ValueError:
                entry[keyword.arg] = "<computed>"
        out.append(entry)
    if not out:
        raise SystemExit(f"generate-registry-artifacts: {source}: no descriptors")
    return out


def _control_absences() -> dict[tuple[str, str], str]:
    """`(surface, unit) -> expires` from the control-parity ledger."""
    import tomllib

    ledger = tomllib.loads(CONTROL_LEDGER.read_text(encoding="utf-8"))
    return {
        (entry["surface"], entry["unit"]): entry["expires"]
        for entry in ledger.get("control", [])
    }


def _control_parity_table() -> str:
    """The docs-side roster. Manifest of what must exist -- never UI.

    PROGRAM.md §6.3 rejects generating SwiftUI or dashboard JS from a
    descriptor and is right: ~1100 of `bf67238`'s 1268 lines were genuine
    per-surface UI work. What is generated here is the *table of obligations*,
    so the doc cannot drift from `CONTROLS` the way five doc rosters drifted
    from the provider registries.
    """
    absences = _control_absences()
    rows = [
        "| Control | Kind | Dashboard | macOS | CLI |",
        "| --- | --- | --- | --- | --- |",
    ]

    def cell(surface: str, key: str, symbol: str) -> str:
        expires = absences.get((surface, key))
        if expires is not None:
            return f"absent — ledgered, expires {expires}"
        return f"`{symbol}`"

    for entry in _control_descriptors(CONTROL_REGISTRY):
        key = entry["key"]
        required = entry.get("surfaces_required", ())
        cli = entry.get("cli", ())
        rows.append(
            "| `{key}` | {kind} | {dash} | {mac} | {cli} |".format(
                key=key,
                kind=entry["kind"],
                dash=(
                    cell("dashboard", key, entry["dashboard_renderer"])
                    if "dashboard" in required
                    else "n/a"
                ),
                mac=(
                    cell("macos", key, entry["swift_symbol"])
                    if "macos" in required
                    else "n/a"
                ),
                cli=", ".join(f"`netllm {c}`" for c in cli) if cli else "n/a",
            )
        )
    return "\n".join(rows) + "\n"


@dataclass(frozen=True)
class Block:
    """One generated region, addressed by a marker id inside a file."""

    path: Path
    marker: str
    render: str

    def rewrite(self, text: str) -> str:
        begin = f"{BEGIN}:{self.marker}"
        end = f"{END}:{self.marker}"
        start_at = text.find(begin)
        end_at = text.find(end)
        if start_at == -1 or end_at == -1:
            raise SystemExit(
                f"generate-registry-artifacts: {self.path.relative_to(ROOT)}: "
                f"missing marker pair for {self.marker!r}. Add\n"
                f"  <comment> {begin}\n  ...\n  <comment> {end}"
            )
        line_end = text.index("\n", start_at) + 1
        return text[:line_end] + self.render + text[text.rindex("\n", 0, end_at) + 1 :]


_DEPRECATION_FIELDS = (
    "id",
    "kind",
    "config_path",
    "symbol",
    "deprecated_in",
    "remove_in",
    "replacement",
    "notes",
)


def _dataclass_rows(source: Path, name: str) -> list[dict[str, str]]:
    """String kwargs of every constructor call in a module-level tuple.

    Same AST-only technique as `_dict_keys` — this script may not import the
    workspace packages (it runs under bare python3 in `run_lint`). Implicitly
    concatenated string literals are joined, because that is how a long `notes`
    is written in the registry.
    """

    def _const(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp):  # not used today; fail loudly if it is
            return None
        return None

    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            first = node.targets[0]
            if isinstance(first, ast.Name):
                target, value = first.id, node.value
        if target != name or not isinstance(value, ast.Tuple):
            continue
        rows: list[dict[str, str]] = []
        for element in value.elts:
            if not isinstance(element, ast.Call):
                continue
            row: dict[str, str] = {}
            for keyword in element.keywords:
                if keyword.arg is None:
                    continue
                text = _const(keyword.value)
                if text is None:
                    text = ast.literal_eval(keyword.value)
                row[keyword.arg] = str(text)
            rows.append(row)
        return rows
    raise SystemExit(f"generate-registry-artifacts: {source}: no tuple named {name}")


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _wrap(value: str, width: int = 74) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _render_deprecations(rows: list[dict[str, str]]) -> str:
    out: list[str] = []
    for row in rows:
        out.append("[[deprecation]]\n")
        for field in _DEPRECATION_FIELDS:
            value = row.get(field, "")
            if field == "notes":
                wrapped = _wrap(value)
                out.append('notes = """\n')
                out.extend(f"{_toml_escape(line)}\n" for line in wrapped)
                out.append('"""\n')
            else:
                out.append(f'{field} = "{_toml_escape(value)}"\n')
        out.append("\n")
    return "".join(out)


def _blocks() -> list[Block]:
    cloud = _dict_keys(CLOUD_REGISTRY, "CLOUD_PROVIDERS")
    local = _dict_keys(LOCAL_REGISTRY, "LOCAL_PROVIDERS")
    deprecations = _dataclass_rows(DEPRECATIONS_PY, "DEPRECATIONS")

    def js_array(ids: list[str], indent: str = "  ") -> str:
        return "".join(f'{indent}"{i}",\n' for i in ids)

    def swift_array(ids: list[str], indent: str = "            ") -> str:
        return indent + ", ".join(f'"{i}"' for i in ids) + ",\n"

    return [
        Block(
            ROOT / "packages/netllm-agent/src/netllm_agent/static/dashboard.js",
            "cloud-provider-ids",
            js_array(cloud),
        ),
        Block(
            ROOT / "packages/netllm-agent/src/netllm_agent/static/dashboard.js",
            "local-provider-ids",
            js_array(local),
        ),
        Block(
            ROOT / "apps/netllm-mac/Sources/Config/KeychainStore.swift",
            "cloud-provider-ids",
            swift_array(cloud),
        ),
        Block(
            ROOT / "config.example.toml",
            "local-provider-ids",
            "providers = [" + ", ".join(f'"{i}"' for i in local) + "]\n",
        ),
        # The deprecation clock, rendered for humans. The registry is the
        # frozen dataclass tuple (same shape as CLOUD_PROVIDERS) because that
        # is what ships in the wheel and what load_config/doctor read; this
        # file is where a person looks up "when does this key go away".
        Block(
            DEPRECATIONS_TOML,
            "deprecations",
            _render_deprecations(deprecations),
        ),
        Block(
            ROOT / "docs/extending/08-control-parity.md",
            "control-parity-table",
            _control_parity_table(),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any generated block is stale, without rewriting",
    )
    args = parser.parse_args()

    stale: list[str] = []
    by_path: dict[Path, str] = {}
    for block in _blocks():
        text = by_path.get(block.path)
        if text is None:
            text = block.path.read_text(encoding="utf-8")
        updated = block.rewrite(text)
        if updated != text:
            stale.append(f"{block.path.relative_to(ROOT)} [{block.marker}]")
        by_path[block.path] = updated

    if args.check:
        if stale:
            print(
                "generate-registry-artifacts: generated blocks are stale:\n  "
                + "\n  ".join(stale)
                + "\n\nRun: python3 scripts/generate-registry-artifacts.py",
                file=sys.stderr,
            )
            return 1
        print(
            f"OK: generate-registry-artifacts — {len(_blocks())} generated blocks "
            "match the registries"
        )
        return 0

    for path, text in by_path.items():
        path.write_text(text, encoding="utf-8")
    if stale:
        print("regenerated:\n  " + "\n  ".join(stale))
    else:
        print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
