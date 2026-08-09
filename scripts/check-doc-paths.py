#!/usr/bin/env python3
"""Every repo path an instructional doc points at must resolve.

``docs/sdk-versions.md`` routed the "Agent" change layer at
``netllm_agent/service.py`` for the whole of the F-24 split, which dissolved
that file into a package. The doc kept reading as authoritative, because a
dead path in prose looks exactly like a live one — and it is the first doc a
new contributor is sent to (docs/extending/PROGRAM.md §2, item 0b.7).

Scope is deliberately the docs that *instruct*. Three kinds of doc quote paths
that are correctly absent and are exempted by prefix below: audit records
describing the tree as it stood, plan documents naming files not yet written,
and evidence maps whose whole point is "this path is dead". Widening the scope
is a real piece of work, not a Phase 0 one-liner.

Run standalone (exit 1 on failure, part of ``scripts/ci.sh lint``):

    python3 scripts/check-doc-paths.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOTS = ("packages/", "apps/", "scripts/", "tests/", "packaging/", "docs/", ".github/")
TOKEN = re.compile(r"`([^`\s]+)`")

EXEMPT_DOCS: dict[str, str] = {
    "docs/architecture/": "audit record — quotes the tree as it stood, not as it is",
    "docs/extending/": "program + evidence maps — quote future and known-dead paths",
    "docs/lint-index.md": "names .gitignore'd coordinator files on purpose",
    "docs/turnstone-lessons.md": "quotes another repository's doc layout",
}
# Build outputs: real paths, produced by a build, absent in a clean checkout.
BUILD_PREFIXES = ("apps/netllm-mac/build/", "packaging/_build/", "packaging/_export/")
# A token naming a shape rather than a file.
PLACEHOLDER = re.compile(r"\.\.\.|X\.Y\.Z|[*?{}<>|$]")


def _candidates(text: str) -> set[str]:
    out: set[str] = set()
    for raw in TOKEN.findall(text):
        token = raw.split(":", 1)[0].rstrip(".,;")
        if not token.startswith(ROOTS) or token.startswith(BUILD_PREFIXES):
            continue
        if PLACEHOLDER.search(token):
            continue
        out.add(token)
    return out


def main() -> int:
    docs = sorted(ROOT.glob("docs/**/*.md")) + [ROOT / "README.md"]
    failures: list[str] = []
    checked = 0
    skipped = 0
    for doc in docs:
        rel = doc.relative_to(ROOT).as_posix()
        if not doc.is_file():
            continue
        if rel.endswith("-plan.md") or any(rel.startswith(p) for p in EXEMPT_DOCS):
            skipped += 1
            continue
        text = doc.read_text(encoding="utf-8")
        for token in sorted(_candidates(text)):
            checked += 1
            if (ROOT / token).exists():
                continue
            lineno = next(
                (i for i, ln in enumerate(text.splitlines(), 1) if token in ln), 0
            )
            failures.append(f"{rel}:{lineno}: `{token}` does not exist")

    if failures:
        print(
            "check-doc-paths: docs reference paths that are not there", file=sys.stderr
        )
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"OK: check-doc-paths — {checked} references resolve "
        f"across {len(docs) - skipped} docs ({skipped} exempt)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
