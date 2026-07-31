"""Contract-vector runner: replay every vector in tests/contract/vectors/.

Record mode: ``NETLLM_VECTOR_RECORD=1 uv run pytest tests/contract/test_vectors.py``
regenerates every vector's "expected" block from live behavior (and still
asserts the freshly recorded expectation replays, so a nondeterministic
vector fails even while recording). Any resulting diff vs git HEAD must be
annotated — see test_divergence_lint.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from canonical import canonicalize_result
from drivers import contract_environment

VECTORS_DIR = Path(__file__).resolve().parent / "vectors"
RECORD = os.environ.get("NETLLM_VECTOR_RECORD") == "1"


def _vector_files() -> list[Path]:
    return sorted(p for p in VECTORS_DIR.glob("*.json"))


def run_vector(doc: dict) -> dict:
    with contract_environment(doc["scenario"]) as env:
        result = env.drive(doc["request"])
        delta = env.probe.delta()
        calls = env.farm.inference_calls()
    return canonicalize_result(result, delta, calls)


@pytest.mark.parametrize("vector_path", _vector_files(), ids=lambda p: p.stem)
def test_contract_vector(vector_path: Path) -> None:
    doc = json.loads(vector_path.read_text())
    actual = run_vector(doc)
    if RECORD:
        doc["expected"] = actual
        vector_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    expected = doc.get("expected")
    assert expected is not None, (
        f"{vector_path.name} has no expected block — record it with "
        "NETLLM_VECTOR_RECORD=1"
    )
    assert actual == expected


def test_vectors_exist() -> None:
    assert _vector_files(), "no contract vectors checked in"


def test_vector_docs_are_well_formed() -> None:
    for path in _vector_files():
        doc = json.loads(path.read_text())
        for key in ("id", "divergence", "scenario", "request"):
            assert key in doc, f"{path.name} missing {key!r}"
        assert isinstance(doc["divergence"], list)
        assert doc["request"].get("path"), f"{path.name} request has no path"
