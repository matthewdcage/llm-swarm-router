"""Ensure vendor SDK imports stay isolated in adapter packages."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = REPO_ROOT / "packages/netllm-core/src/netllm_core"
FORBIDDEN = frozenset({"openai", "anthropic"})


def _imports_vendor_sdk(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in FORBIDDEN:
                    found.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in FORBIDDEN:
                found.append(node.module)
    return found


@pytest.mark.parametrize(
    "py_file",
    sorted(CORE_SRC.rglob("*.py")),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_netllm_core_does_not_import_vendor_sdks(py_file: Path) -> None:
    violations = _imports_vendor_sdk(py_file)
    assert not violations, (
        f"{py_file.relative_to(REPO_ROOT)} must not import vendor SDKs: {violations}"
    )
