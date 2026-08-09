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


def _js_object_after(rel_path: str, marker: str) -> tuple[dict[str, str], str]:
    """Parse the first brace-balanced `{...}` literal following `marker`."""
    text, path = _read(rel_path)
    start = text.find(marker)
    assert start != -1, f"{rel_path}: marker not found: {marker!r}"
    open_at = text.find("{", start)
    assert open_at != -1, f"{rel_path}: no '{{' after marker {marker!r}"
    depth = 0
    close_at = -1
    for index in range(open_at, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                close_at = index
                break
    assert close_at != -1, f"{rel_path}: unterminated '{{' after {marker!r}"
    inner = text[open_at + 1 : close_at]
    pairs = re.findall(
        r"""^\s*["']?([A-Za-z_][A-Za-z0-9_-]*)["']?\s*:\s*([^,\n]+?)\s*,?\s*$""",
        inner,
        re.M,
    )
    return {
        key: value for key, value in pairs
    }, f"{path.name}:{_line_of(text, open_at)}"


def dict_keys_after(rel_path: str, marker: str) -> Projection:
    """Keys of the first object literal after `marker`.

    `TAB_RENDERERS` in dashboard.js is the shape this exists for -- a map of
    tab key -> renderer function, which neither `string_array_after` (no
    brackets) nor `switch_case_labels` (no `case`) can read.

    Anchored on the identifier, never a line number. PROGRAM.md cites
    `TAB_RENDERERS` at `:2499`; this docstring corrected that to `:2524` in
    Phase 4; by Phase 8 the symbol had moved again. Both numbers were true
    when written and neither is now, which is the entire argument for
    searching for the marker and *reporting* the line rather than asserting
    one.
    """
    pairs, location = _js_object_after(rel_path, marker)
    return Projection(tuple(pairs), location)


def dict_values_after(rel_path: str, marker: str) -> Projection:
    """Values of the first object literal after `marker` (bare identifiers)."""
    pairs, location = _js_object_after(rel_path, marker)
    return Projection(tuple(pairs.values()), location)


def attribute_values(rel_path: str, attribute: str, prefix: str = "") -> Projection:
    """Values of every `attribute="…"` in an HTML file, optionally prefixed.

    `index.html`'s `data-tab="…"` buttons and `id="tab-…"` sections are the
    dashboard's two other presence units for a tab, and they are attributes,
    not literals in an array or an object.
    """
    text, path = _read(rel_path)
    pattern = re.compile(re.escape(attribute) + r'="([^"]*)"')
    match = pattern.search(text)
    line = _line_of(text, match.start()) if match else 1
    values = tuple(v for v in pattern.findall(text) if v.startswith(prefix))
    return Projection(values, f"{path.name}:{line}")


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"^[ \t]*(?://|///).*$", re.M)
_TRAILING_COMMENT = re.compile(r"(?<![:\"'/])//(?![/\"']).*$", re.M)


def strip_comments(text: str) -> str:
    """Remove `//`, `///` and `/* */` comments from JS or Swift source.

    A guard that scans source text for a name must not count prose: a
    comment or docstring naming a control is not that control, and a scan
    that forgets this fires green on its own changelog. This repo has made
    that mistake twice.

    Deliberately conservative -- it does not attempt to parse strings, so a
    `//` inside a string literal that is not a URL could over-strip. Every
    caller here re-checks its expectation against a known-red case, and the
    `dashboard.js` node --check test still parses the real file.
    """
    text = _BLOCK_COMMENT.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    return _TRAILING_COMMENT.sub("", text)


@dataclass(frozen=True)
class SourceRegion:
    """A comment-stripped slice of a surface file, plus where it starts."""

    text: str
    location: str

    def has_symbol(self, name: str) -> bool:
        """`name` used as a quoted key or a property access, not as prose.

        Both surfaces bind config fields either as `"field_name"` (JS schema
        lookups, Swift `bool("menubar_show_cpu")`) or as `.field_name`
        (`$model.document.agent.max_concurrency`). Requiring one of those two
        shapes is what keeps a sentence like "an above-default
        max_concurrency" in a user-facing string from reading as a control.
        """
        return f'"{name}"' in self.text or f".{name}" in self.text

    def contains(self, fragment: str) -> bool:
        return fragment in self.text


def source_region(
    rel_path: str, start_marker: str | None = None, end_marker: str | None = None
) -> SourceRegion:
    """The comment-stripped text between two identifiers in a surface file.

    `start_marker=None` means the top of the file: some questions are about
    the whole surface (does this function exist at all) rather than about one
    renderer's span.
    """
    text, path = _read(rel_path)
    if start_marker is None:
        start_marker = ""
    start = text.find(start_marker)
    assert start != -1, f"{rel_path}: region start not found: {start_marker!r}"
    if end_marker is None:
        end = len(text)
    else:
        end = text.find(end_marker, start + len(start_marker))
        assert end != -1, (
            f"{rel_path}: region end not found after {start_marker!r}: {end_marker!r}"
        )
    return SourceRegion(
        strip_comments(text[start:end]), f"{path.name}:{_line_of(text, start)}"
    )


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
