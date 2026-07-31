"""Anti-collapse guard: a keyless-401 may never be recorded by accident.

The Messages surfaces answer ``401 {"type":"error","error":{"type":
"authentication_error","message":"ANTHROPIC_API_KEY required ..."}}`` with
**zero upstream calls** whenever no candidate backend matches the requested
model (behavior-matrix D11). That outcome is a legitimate contract in a
handful of vectors — and a silent trap everywhere else: a scenario whose
farm serves ``farm-chat`` while the request asks for ``claude-farm`` with no
alias bridging them never reaches the farm at all, so the vector records the
keyless 401 instead of the translated-arm / flattening behavior its note
claims. Three of the eight keyless-401 recordings in the corpus were of that
kind before this guard existed.

Contract enforced here
----------------------
Every vector whose *declaration* does not say it is about a keyless 401 must
record something else. The declaration is the ``note`` (or the vector id) —
deliberately **not** the ``divergence`` list: the three false vectors all
carried D9/D11 already, so a D-ID exemption would have blessed exactly the
recordings this guard exists to catch. Say the word ``keyless`` in the note
(or the id) to opt a vector in.

The guard runs at record time and at replay time (``assert_no_unintended_
keyless_401`` on the freshly produced result, plus
``assert_corpus_has_no_unintended_keyless_401`` over every checked-in
``expected`` block), so it fails a bad recording the moment it is made and
keeps failing while it stays checked in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_KEYLESS_DECLARATION = "keyless"
_KEYLESS_BODY_MARKER = "ANTHROPIC_API_KEY"


def declares_keyless_401(doc: dict[str, Any]) -> bool:
    """True if the vector *says* a keyless 401 is what it pins."""
    text = f"{doc.get('id', '')} {doc.get('note') or ''}".lower()
    return _KEYLESS_DECLARATION in text


def is_keyless_401(expected: dict[str, Any]) -> bool:
    """True if a recorded expectation is the collapsed keyless-401 outcome."""
    if expected.get("status") != 401:
        return False
    if expected.get("upstream_calls"):
        return False
    payload = json.dumps(
        [expected.get("body_shape"), expected.get("sse_frames")], default=str
    )
    return _KEYLESS_BODY_MARKER in payload


def assert_no_unintended_keyless_401(
    doc: dict[str, Any], expected: dict[str, Any]
) -> None:
    """Fail unless a keyless-401 recording was declared by the vector."""
    if not is_keyless_401(expected):
        return
    assert declares_keyless_401(doc), (
        f"{doc.get('id')}: recorded a keyless 401 (status 401, zero upstream "
        f"calls, {_KEYLESS_BODY_MARKER} in the body) but its note/id never "
        "mentions one. The request almost certainly never reached the farm — "
        "check that the requested model resolves to a served model (e.g. a "
        "routing.model_aliases entry). If the keyless 401 really is the "
        f"contract, say '{_KEYLESS_DECLARATION}' in the note."
    )


def assert_corpus_has_no_unintended_keyless_401(paths: list[Path]) -> None:
    """Replay-time sweep over checked-in vectors' ``expected`` blocks."""
    for path in paths:
        doc = json.loads(path.read_text())
        expected = doc.get("expected")
        if isinstance(expected, dict):
            assert_no_unintended_keyless_401(doc, expected)
