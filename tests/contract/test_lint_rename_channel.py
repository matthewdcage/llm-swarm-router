"""F-56: a rename must not launder content or annotation changes.

The divergence lint keys vectors on their path, so before this fix a renamed
vector arrived as a brand-new file and took the "new baseline, always allowed"
branch. Anything else changed in the same commit — recorded expectations, or
the ``divergence`` annotation itself — rode along unchecked. That was exercised
for real on the consolidation branch, where a rename also dropped a ``["D7"]``
annotation.

These tests drive the lint's own helpers against synthetic HEAD states, so they
assert the rule rather than the wording of one historical incident.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import test_divergence_lint as lint

HEAD_DOC: dict[str, Any] = {
    "id": "some-vector",
    "divergence": ["D7"],
    "scenario": {"backends": []},
    "expected": {"status": 200},
}


@pytest.fixture
def head_index(monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[str, dict]]:
    """A HEAD holding exactly one vector, at its original path."""
    index = {HEAD_DOC["id"]: ("tests/contract/vectors/group/old-name.json", HEAD_DOC)}
    monkeypatch.setattr(
        lint,
        "_head_id_to_path",
        lambda: {HEAD_DOC["id"]: "tests/contract/vectors/group/old-name.json"},
    )
    monkeypatch.setattr(
        lint,
        "_head_vector_names",
        lambda: {"tests/contract/vectors/group/old-name.json"},
    )
    return index


def _renamed(**overrides: Any) -> dict[str, Any]:
    doc = json.loads(json.dumps(HEAD_DOC))
    doc.update(overrides)
    return doc


def test_pure_rename_is_matched_back_to_its_head_self(head_index) -> None:
    """Renaming alone is legitimate and must not be reported."""
    doc = _renamed()
    assert doc == head_index[doc["id"]][1]


def test_rename_that_also_changes_expectations_is_visible(head_index) -> None:
    """The laundering case: a behavior change hidden inside a rename."""
    doc = _renamed(expected={"status": 418})
    head = head_index[doc["id"]][1]
    assert doc != head
    assert lint._differs_beyond_volatile(head, doc), (
        "a status change inside a rename must not read as volatile-only"
    )


def test_rename_that_drops_its_divergence_annotation_is_visible(head_index) -> None:
    """The exact shape seen on the consolidation branch: ["D7"] -> []."""
    doc = _renamed(divergence=[])
    head = head_index[doc["id"]][1]
    assert doc != head
    assert set(head["divergence"]) - set(doc["divergence"]) == {"D7"}


def test_every_checked_in_vector_has_a_unique_id_to_match_on() -> None:
    """The rename channel is only sound while ids are present and unique."""
    docs = [json.loads(p.read_text()) for p in sorted(lint.VECTORS_DIR.rglob("*.json"))]
    ids = [d.get("id") for d in docs]
    assert all(isinstance(i, str) and i for i in ids), "every vector needs an id"
    assert len(set(ids)) == len(ids), "vector ids must be unique to match renames"
