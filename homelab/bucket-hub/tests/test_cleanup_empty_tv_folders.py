import hashlib
import json
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
SCRIPT = HUB / "scripts" / "cleanup-empty-tv-folders.py"


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "movies"
    s01 = root / "torrents" / "Breaking Bad" / "Season 01"
    s02 = root / "torrents" / "Breaking Bad" / "Season 02"
    rel = root / "torrents" / "Breaking.Bad.S01.1080p"
    s01.mkdir(parents=True)
    s02.mkdir(parents=True)
    (s02 / "ep.mkv").write_bytes(b"x")
    rel.mkdir(parents=True)
    (rel / "ep.mkv").write_bytes(b"y")
    return root


def _library(jobs: Path) -> Path:
    jobs.mkdir(parents=True, exist_ok=True)
    lib = {
        "drives": {
            "ext_movies": {
                "folders": [
                    {"id": "s01", "rel": "torrents/Breaking Bad/Season 01"},
                    {"id": "s02", "rel": "torrents/Breaking Bad/Season 02"},
                ]
            }
        }
    }
    path = jobs / "library.json"
    path.write_text(json.dumps(lib), encoding="utf-8")
    return path


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_dry_run_lists_only_empty(tmp_path):
    root = _tree(tmp_path)
    jobs = tmp_path / "jobs"
    lib = _library(jobs)
    before = hashlib.sha256(lib.read_bytes()).hexdigest()
    r = _run(
        [
            "--jobs-dir",
            str(jobs),
            "--root",
            str(root),
            "--allow-any-root",
        ]
    )
    assert r.returncode == 0, r.stderr
    assert "Season 01" in r.stdout
    assert "Season 02" not in r.stdout
    assert (root / "torrents" / "Breaking Bad" / "Season 01").is_dir()
    assert hashlib.sha256(lib.read_bytes()).hexdigest() == before


def test_apply_disk_only(tmp_path):
    root = _tree(tmp_path)
    jobs = tmp_path / "jobs"
    _library(jobs)
    r = _run(
        [
            "--jobs-dir",
            str(jobs),
            "--root",
            str(root),
            "--allow-any-root",
            "--apply",
            "--disk-only",
        ]
    )
    assert r.returncode == 0, r.stderr
    assert not (root / "torrents" / "Breaking Bad" / "Season 01").exists()
    assert (root / "torrents" / "Breaking Bad" / "Season 02" / "ep.mkv").is_file()
    lib = json.loads((jobs / "library.json").read_text())
    rels = [f["rel"] for f in lib["drives"]["ext_movies"]["folders"]]
    assert "torrents/Breaking Bad/Season 01" in rels


def test_apply_library_only(tmp_path):
    root = _tree(tmp_path)
    jobs = tmp_path / "jobs"
    _library(jobs)
    r = _run(
        [
            "--jobs-dir",
            str(jobs),
            "--root",
            str(root),
            "--allow-any-root",
            "--apply",
            "--library-only",
        ]
    )
    assert r.returncode == 0, r.stderr
    assert (root / "torrents" / "Breaking Bad" / "Season 01").is_dir()
    lib = json.loads((jobs / "library.json").read_text())
    rels = [f["rel"] for f in lib["drives"]["ext_movies"]["folders"]]
    assert "torrents/Breaking Bad/Season 01" not in rels
    assert "torrents/Breaking Bad/Season 02" in rels


def test_refuses_root_outside_movies(tmp_path):
    r = _run(["--jobs-dir", str(tmp_path), "--root", str(tmp_path / "elsewhere")])
    assert r.returncode == 2
