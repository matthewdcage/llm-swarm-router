#!/usr/bin/env python3
"""No-new-mirrors gate (docs/extending/PROGRAM.md §2, item 0a).

The audit's recurring failure is "added to one surface, forgotten in the
others": a provider id is stated in its registry and then re-stated, by hand,
in a dashboard bootstrap, a Swift fallback, a TOML example and a Literal. Each
copy is a place a *new* entry can be forgotten, and forgetting is silent.

This gate does not delete the copies — the later phases do that. It draws the
live id list out of each registry and fails if one of those ids appears in a
file that ``tests/conformance/ledgers/mirrors.toml`` does not already name. So
the mirrors that exist today stay green (each carries a ``reason`` and the
``expires`` phase that removes it) and a new id cannot land in a new file.

Ids are read from the registry source by AST, never by importing it: this runs
under bare ``python3`` in ``run_lint`` alongside the other gates, with no
installed environment.

Run standalone (exit 1 on failure, part of ``scripts/ci.sh lint``):

    python3 scripts/check-registry-mirrors.py
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "tests/conformance/ledgers/mirrors.toml"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _assigned_value(tree: ast.Module, name: str) -> ast.expr | None:
    """The right-hand side of a module-level ``name = ...`` (annotated or not)."""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
            if isinstance(target, ast.Name) and target.id == name and value is not None:
                return value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    return None


def _extract(source: Path, spec: str) -> list[str]:
    """Pull the live id list out of a registry module without importing it.

    ``dict-keys:NAME``  — the string keys of a module-level dict literal.
    ``tuple-heads:NAME`` — the first element of each tuple in a list literal.
    """
    kind, _, name = spec.partition(":")
    value = _assigned_value(_module(source), name)
    if value is None:
        raise SystemExit(f"check-registry-mirrors: {source}: no module-level {name}")

    ids: list[str] = []
    if kind == "dict-keys":
        if not isinstance(value, ast.Dict):
            raise SystemExit(f"check-registry-mirrors: {name} is not a dict literal")
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                ids.append(key.value)
    elif kind == "tuple-heads":
        if not isinstance(value, (ast.List, ast.Tuple)):
            raise SystemExit(f"check-registry-mirrors: {name} is not a list literal")
        for element in value.elts:
            if isinstance(element, (ast.Tuple, ast.List)) and element.elts:
                head = element.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str):
                    ids.append(head.value)
    else:
        raise SystemExit(f"check-registry-mirrors: unknown extract kind {kind!r}")

    if not ids:
        raise SystemExit(f"check-registry-mirrors: {name} yielded no ids")
    return ids


def _hits(path: Path, ids: list[str]) -> list[tuple[int, str]]:
    """Quoted occurrences of any id, as ``(line_number, id)``.

    Quotes are the whole heuristic and that is deliberate: a bare word in a
    comment is prose, ``"ollama"`` is a fact. No AST for the general case,
    because four of the scanned files are not Python.

    Python files get one refinement: comments and docstrings are blanked
    first. A docstring explaining *why* a special case was removed quotes the
    id it removed, and flagging that is a false positive -- which is worse
    than useless, because the cheapest way to silence it is a ledger entry,
    and the ledger is supposed to mean "a real mirror lives here". Prose about
    a fact is not a copy of it.
    """
    pattern = re.compile("|".join(f"[\"']{re.escape(i)}[\"']" for i in sorted(ids)))
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        text = _blank_python_prose(text)
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in pattern.finditer(line):
            found.append((lineno, match.group(0)[1:-1]))
    return found


def _blank_python_prose(text: str) -> str:
    """Replace comments and docstrings with blanks, preserving line numbers.

    Uses ``tokenize`` rather than a regex so an id inside a normal string
    literal still counts -- only COMMENT tokens and STRING tokens standing
    alone as an expression statement (i.e. docstrings) are removed.
    """
    import io
    import tokenize

    lines = text.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text  # unparseable: scan it verbatim rather than skip it

    spans: list[tuple[int, int, int, int]] = []
    prev_meaningful: int | None = None
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            spans.append((*tok.start, *tok.end))
            continue
        if tok.type == tokenize.STRING and prev_meaningful in (
            None,
            tokenize.INDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        ):
            spans.append((*tok.start, *tok.end))
        if tok.type not in (
            tokenize.NL,
            tokenize.COMMENT,
            tokenize.INDENT,
            tokenize.DEDENT,
        ):
            prev_meaningful = tok.type

    for srow, scol, erow, ecol in spans:
        for row in range(srow, erow + 1):
            line = lines[row - 1]
            start = scol if row == srow else 0
            end = ecol if row == erow else len(line.rstrip("\n"))
            keep_nl = "\n" if line.endswith("\n") else ""
            body = line.rstrip("\n")
            lines[row - 1] = body[:start] + " " * (end - start) + body[end:] + keep_nl
    return "".join(lines)


def main() -> int:
    ledger = tomllib.loads(LEDGER.read_text(encoding="utf-8"))
    scan_files = [Path(p) for p in ledger["scan"]["files"]]

    missing = [str(p) for p in scan_files if not (ROOT / p).is_file()]
    if missing:
        print(
            "check-registry-mirrors: ledger [scan].files names missing paths:\n  "
            + "\n  ".join(missing),
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    for fact_class in ledger["fact_class"]:
        class_id = fact_class["id"]
        source = Path(fact_class["source"])
        ids = _extract(ROOT / source, fact_class["extract"])
        allowed = {source} | {
            Path(entry["glob"]) for entry in fact_class.get("allowed_mirrors", [])
        }
        for rel in scan_files:
            if rel in allowed:
                continue
            for lineno, ident in _hits(ROOT / rel, ids):
                failures.append(
                    f"{rel}:{lineno}: {class_id} '{ident}' mirrored outside "
                    f"{source} — derive it, or ledger the file in "
                    f"{LEDGER.relative_to(ROOT)} with a reason and an expiry"
                )

    if failures:
        print("check-registry-mirrors: new registry mirrors found", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1

    total = sum(
        len(_extract(ROOT / Path(fc["source"]), fc["extract"]))
        for fc in ledger["fact_class"]
    )
    print(
        f"OK: check-registry-mirrors — {total} ids across "
        f"{len(ledger['fact_class'])} fact classes, {len(scan_files)} files scanned"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
