from __future__ import annotations

import ast
import subprocess
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
MANIFEST = HUB / "scripts" / "deploy-manifest.txt"


def _parse_manifest() -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"pi": [], "worker": []}
    cur = None
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "[pi]":
            cur = "pi"
            continue
        if line == "[worker]":
            cur = "worker"
            continue
        if cur:
            sections[cur].append(line)
    return sections


def _local_modules(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n for n in names if (HUB / f"{n}.py").is_file()}


def test_manifest_covers_app_and_worker_imports():
    sections = _parse_manifest()
    app_mods = _local_modules(HUB / "app.py")
    worker_mods = _local_modules(HUB / "hub-worker.py")
    pi = set(sections["pi"])
    worker = set(sections["worker"])
    missing_pi = {f"{m}.py" for m in app_mods} - pi
    missing_w = {f"{m}.py" for m in worker_mods} - worker
    assert not missing_pi, missing_pi
    assert not missing_w, missing_w


def test_manifest_paths_exist():
    sections = _parse_manifest()
    missing = [p for rows in sections.values() for p in rows if not (HUB / p).is_file()]
    assert not missing, missing


def test_verify_script_syntax():
    r = subprocess.run(
        ["bash", "-n", str(HUB / "scripts" / "verify-deploy-parity.sh")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
