#!/usr/bin/env python3
"""List (and optionally remove) empty torrents/<Show>/Season * folders.

Dry-run by default. Never deletes a non-empty directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

DEFAULT_ROOT = "/mnt/external/movies"
SEASON_NAME = re.compile(r"^Season\s+\d", re.IGNORECASE)


def _atomic_write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".library.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_empty_season_dirs(root: Path) -> list[Path]:
    torrents = root / "torrents"
    if not torrents.is_dir():
        return []
    empty: list[Path] = []
    for show in sorted(torrents.iterdir()):
        if not show.is_dir() or show.name.startswith("."):
            continue
        for child in sorted(show.iterdir()):
            if not child.is_dir() or not SEASON_NAME.match(child.name):
                continue
            leftovers = [p for p in child.iterdir() if p.name not in {".", ".."}]
            if not leftovers:
                empty.append(child)
    return empty


def library_season_records(library: dict, empty_rels: set[str]) -> list[dict]:
    hits: list[dict] = []
    drives = library.get("drives") or {}
    if isinstance(drives, list):
        return hits
    for drive in drives.values():
        for folder in drive.get("folders") or []:
            rel = str(folder.get("rel") or "")
            if rel in empty_rels:
                hits.append(folder)
    return hits


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--jobs-dir", required=True)
    p.add_argument("--root", default=DEFAULT_ROOT)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--library-only", action="store_true")
    p.add_argument("--disk-only", action="store_true")
    p.add_argument("--allow-any-root", action="store_true")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    allowed = Path(DEFAULT_ROOT).resolve()
    if root != allowed and not str(root).startswith(str(allowed) + os.sep):
        if not args.allow_any_root:
            print(
                f"refusing --root {root} (expected {DEFAULT_ROOT}); "
                "pass --allow-any-root for tests",
                file=sys.stderr,
            )
            return 2

    empty = list_empty_season_dirs(root)
    for folder in empty:
        try:
            rel = folder.relative_to(root)
        except ValueError:
            rel = folder
        print(rel)

    jobs_dir = Path(args.jobs_dir)
    lib_path = jobs_dir / "library.json"
    library: dict = {}
    if lib_path.is_file():
        library = json.loads(lib_path.read_text(encoding="utf-8"))

    empty_rels = set()
    for folder in empty:
        try:
            empty_rels.add(str(folder.relative_to(root)))
        except ValueError:
            empty_rels.add(folder.name)

    records = library_season_records(library, empty_rels)
    for rec in records:
        print(f"library:{rec.get('rel')}")

    if not args.apply:
        return 0

    if not args.library_only:
        for folder in empty:
            folder.rmdir()
    if not args.disk_only and lib_path.is_file():
        drop = empty_rels
        drives = library.get("drives") or {}
        if isinstance(drives, dict):
            for drive in drives.values():
                folders = drive.get("folders") or []
                drive["folders"] = [f for f in folders if str(f.get("rel") or "") not in drop]
            _atomic_write_json(lib_path, library)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
