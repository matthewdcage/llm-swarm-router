"""`compare_versions` against the shared corpus, plus the Swift-parity gate.

Deliberately NOT under `tests/contract/`: that directory's suite is a fixed
373-test golden-vector gate and this is not a vector. Only the corpus JSON
lives there, next to `routes.json`, because it is a cross-language contract
artifact rather than a Python fixture.

Why a corpus at all (PROGRAM.md §4): version ordering is implemented twice —
once in Python, once in the macOS app — and the two had nothing in common to
disagree against. `compare_versions` scraped every digit out of a string and
compared the resulting list, so `0.5.0rc1` became `[0, 5, 0, 1]`: newer than
`0.5.0` and exactly equal to `0.5.0.1`. That was invisible while
`fetch_latest_release` filtered prereleases out, and load-bearing the moment
an operator actually *ran* one — the mesh then told them the wrong machine
was behind.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from netllm_core.update import compare_versions

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = REPO_ROOT / "tests/contract/version-ordering.json"
SWIFT_IMPL = REPO_ROOT / "apps/netllm-mac/Sources/Config/VersionOrdering.swift"
SWIFT_TEST = (
    REPO_ROOT / "apps/netllm-mac/Tests/NetllmMacTests/VersionOrderingTests.swift"
)


def _corpus() -> list[dict]:
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return data["cases"]


CASES = _corpus()


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['left'] or '<empty>'}_vs_{c['right']}" for c in CASES]
)
def test_compare_versions_matches_the_shared_corpus(case: dict) -> None:
    got = _sign(compare_versions(case["left"], case["right"]))
    assert got == case["expect"], (
        f"compare_versions({case['left']!r}, {case['right']!r}) = {got}, "
        f"corpus says {case['expect']}: {case['why']}"
    )


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['right']}_vs_{c['left'] or '<empty>'}" for c in CASES]
)
def test_compare_versions_is_antisymmetric(case: dict) -> None:
    """compare(b, a) == -compare(a, b) for every corpus pair.

    Free to assert and it is the property that makes "which peer is older"
    well-defined at all; the corpus only states one direction per pair.
    """
    forward = _sign(compare_versions(case["left"], case["right"]))
    backward = _sign(compare_versions(case["right"], case["left"]))
    assert backward == -forward, (
        f"{case['left']!r} vs {case['right']!r} orders {forward} one way and "
        f"{backward} the other"
    )


def test_every_corpus_case_states_a_reason() -> None:
    for case in CASES:
        assert case["why"].strip(), f"{case} has no stated reason"
        assert case["expect"] in (-1, 0, 1), f"{case}: expect must be a sign"


# --- the "Python and Swift cannot disagree" half --------------------------
#
# `swift test` runs only on the macos-14 job, so a Linux CI run cannot execute
# the Swift assertions. What it CAN do is prove the Swift side is wired to the
# same corpus and recognises the same prerelease vocabulary — a projection
# test in the PROGRAM.md §1 sense. It proves presence, not behaviour (risk R3);
# the behaviour is asserted by VersionOrderingTests.swift on macOS.


def test_swift_comparator_consumes_the_same_corpus() -> None:
    assert SWIFT_IMPL.is_file(), f"missing {SWIFT_IMPL}"
    assert SWIFT_TEST.is_file(), f"missing {SWIFT_TEST}"
    text = SWIFT_TEST.read_text(encoding="utf-8")
    assert "version-ordering.json" in text, (
        f"{SWIFT_TEST.name} no longer reads the shared corpus — it is back to "
        "asserting a hardcoded list, which is the drift this file exists to stop"
    )


def test_swift_prerelease_vocabulary_matches_python() -> None:
    """Both sides must rank the same labels, or a `0.5.0rc1` peer orders
    differently on macOS than on the agent that warned about it."""
    from netllm_core.update import _PRERELEASE_RANK

    swift = SWIFT_IMPL.read_text(encoding="utf-8")
    swift_labels = set(re.findall(r'"([a-z]+)":\s*-?\d+', swift))
    assert swift_labels == set(_PRERELEASE_RANK), (
        f"{SWIFT_IMPL.name} ranks {sorted(swift_labels)} but Python ranks "
        f"{sorted(_PRERELEASE_RANK)}"
    )
