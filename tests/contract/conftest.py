"""Contract-harness fixtures (plan-f24-f26.md §2, Phase 0).

Makes the sibling helper modules (farm, probe, drivers, canonical)
importable without packaging tests/, and exposes the environment builder
as a fixture for the characterization tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from drivers import ContractEnv, contract_environment  # noqa: E402

CONTRACT_DIR = _HERE
VECTORS_DIR = _HERE / "vectors"
ALLOWED_DIVERGENCES_FILE = _HERE / "allowed-divergences.txt"


@pytest.fixture
def contract_env():
    """Builder fixture: ``env = contract_env(scenario)``; auto-teardown."""
    from contextlib import ExitStack

    with ExitStack() as stack:

        def _build(scenario: dict) -> ContractEnv:
            return stack.enter_context(contract_environment(scenario))

        yield _build
