"""Read a fact back out of a non-Python surface, with its source location.

A *projection* is a place a registry fact has to appear that cannot import
the registry: Swift, JavaScript, TOML examples, Markdown roster tables.
Phases 3-4 make several of these generated; until then they are
projection-tested, which is the middle rung of PROGRAM.md §1's ladder
(derive > generate with --check > projection-test > mirror, which fails).

Every helper returns `(values, source_location)` so a kit failure reads

    SettingsViewModel.swift:94 is missing 'dashscope'

rather than "assert set1 == set2". The location is what makes a red test
actionable by someone who has never seen the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Projection:
    """Values found in a non-Python surface, plus where they came from."""

    values: tuple[str, ...]
    location: str

    def missing(self, expected: set[str]) -> set[str]:
        return expected - set(self.values)

    def unexpected(self, expected: set[str]) -> set[str]:
        return set(self.values) - expected

    def assert_covers(self, expected: set[str]) -> None:
        missing = self.missing(expected)
        assert not missing, f"{self.location} is missing {sorted(missing)}"

    def assert_exactly(self, expected: set[str]) -> None:
        self.assert_covers(expected)
        extra = self.unexpected(expected)
        assert not extra, f"{self.location} has unexpected {sorted(extra)}"


def _read(rel_path: str) -> tuple[str, Path]:
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"projection source missing: {rel_path}"
    return path.read_text(encoding="utf-8"), path


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def string_array_after(rel_path: str, marker: str) -> Projection:
    """Quoted strings in the first `[...]` literal following `marker`.

    Covers the Swift `static let xs = [ "a", "b" ]` and JS `const xs = [...]`
    shapes uniformly -- both are quoted, comma-separated, bracket-delimited.
    """
    text, path = _read(rel_path)
    start = text.find(marker)
    assert start != -1, f"{rel_path}: marker not found: {marker!r}"
    open_at = text.find("[", start)
    assert open_at != -1, f"{rel_path}: no '[' after marker {marker!r}"
    close_at = text.find("]", open_at)
    assert close_at != -1, f"{rel_path}: unterminated '[' after {marker!r}"
    inner = text[open_at + 1 : close_at]
    values = tuple(re.findall(r"""["']([^"']+)["']""", inner))
    return Projection(values, f"{path.name}:{_line_of(text, open_at)}")


def switch_case_labels(rel_path: str, marker: str) -> Projection:
    """String labels of the `case "x":` arms in the switch following `marker`.

    Swift `switch` over provider ids. The `default:` arm is not a value and is
    excluded -- it is usually the generic behaviour the cases are redundant
    with, which is why Axis A deletes them.
    """
    text, path = _read(rel_path)
    start = text.find(marker)
    assert start != -1, f"{rel_path}: marker not found: {marker!r}"
    end = text.find("\n    }", start)
    body = text[start : end if end != -1 else len(text)]
    values = tuple(re.findall(r'case\s+"([^"]+)"', body))
    return Projection(values, f"{path.name}:{_line_of(text, start)}")


def toml_table_ids(rel_path: str, prefix: str) -> Projection:
    """Ids from `[prefix.<id>]` headers, e.g. `[cloud.providers.openai]`."""
    text, path = _read(rel_path)
    pattern = re.compile(
        r"^\s*\[+" + re.escape(prefix) + r"\.([A-Za-z0-9_-]+)\]+", re.MULTILINE
    )
    match = pattern.search(text)
    line = _line_of(text, match.start()) if match else 1
    return Projection(tuple(pattern.findall(text)), f"{path.name}:{line}")


def markdown_table_column(rel_path: str, heading: str, column: int = 0) -> Projection:
    """Cells from one column of the first Markdown table after `heading`.

    Backticks and bold markers are stripped, so `` `omlx` `` reads as `omlx`.
    Axis A's five doc rosters need this; Phase 4 generates them instead.
    """
    text, path = _read(rel_path)
    start = text.find(heading)
    assert start != -1, f"{rel_path}: heading not found: {heading!r}"
    values: list[str] = []
    first_row = 0
    for raw in text[start:].splitlines()[1:]:
        line = raw.strip()
        if not line.startswith("|"):
            if values:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or set(cells[0]) <= set("-: "):
            continue
        if column >= len(cells):
            continue
        cell = cells[column].strip("`* ")
        if cell and cell.lower() not in ("id", "provider", "name"):
            if not first_row:
                first_row = _line_of(text, start)
            values.append(cell)
    return Projection(tuple(values), f"{path.name}:{first_row or 1}")
