"""Exhaustive error-taxonomy table: surface x failure mode (Phase 3).

Plan §3 Phase 3's exit criterion. Every one of the seven request paths is
driven through the real routes for every terminal failure mode, and the
resulting HTTP status *and* error-envelope shape are asserted from one
table. The table is the readable form of the taxonomy: read down a column
to see where the surfaces disagree.

Failure modes
-------------
``capacity``            upstream 429 (capacity-classified, pool.py p:39-55)
                        with no other candidate — the request still fails.
``exhausted_error``     upstream 500; candidates exhausted with an error.
``exhausted_keyless``   nothing serves the requested name and no cloud
                        credential is present — the D11 split: 401 on
                        Messages, 404-model-not-found on every OpenAI
                        surface.
``exhausted_keyed``     same, but the Messages caller did send an
                        ``x-api-key`` (with ``local-only`` so no cloud row
                        is materialized) — the taxonomy's other Messages
                        branch, a status-less "no healthy backends".
``upstream_client_err`` upstream 404. Phase 3's semantic flip (D11): the
                        Messages route now forwards it like the OpenAI
                        surfaces instead of flattening it to 502.

Envelope shapes come from errors.py (F-38) and are keyed on the request
path, not on the exception type — which is why a Messages response to an
``OpenAIUpstreamError`` is still Anthropic-shaped.
"""

from __future__ import annotations

from typing import Any

import pytest
from drivers import PATHS, contract_environment

MSG_PATHS = ("messages_ns", "messages_s")
CHAT_MODEL = "farm-chat"
EMB_MODEL = "farm-embed"
MSG_MODEL = "claude-farm"
UNKNOWN = "no-such-model-anywhere"
# [D4] /v1/embeddings gained a capability guard in Phase 4c, and
# model_capability() reads unknown names as "chat". A name with no embedding
# token would now be rejected by the guard (400) before the loop ever runs,
# which would test the guard instead of the exhaustion taxonomy. The emb rows
# use an unknown name the guard lets through so they keep pinning what they
# were written to pin.
UNKNOWN_EMB = "no-such-embed-model-anywhere"

# Messages requests are authored against MSG_MODEL; an openai-format farm
# row serves CHAT_MODEL. The alias bridges them so the *translated* arm is
# what fails, which is the arm D11 is about.
_ALIAS = {MSG_MODEL: [CHAT_MODEL]}


def _script(status: int) -> list[dict[str, Any]]:
    return [{"behavior": "http", "status": status}]


def _body(path: str, model: str) -> dict[str, Any]:
    if path == "emb":
        return {"model": model, "input": "hi"}
    if path in MSG_PATHS:
        return {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
        }
    if path.startswith("responses"):
        return {"model": model, "input": "hi"}
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


def _served(path: str) -> str:
    return EMB_MODEL if path == "emb" else CHAT_MODEL


def _unknown(path: str) -> str:
    return UNKNOWN_EMB if path == "emb" else UNKNOWN


def _requested(path: str) -> str:
    if path in MSG_PATHS:
        return MSG_MODEL
    return _served(path)


# mode -> (upstream script status | None, requested model, extra headers)
_MODES: dict[str, tuple[int | None, bool, dict[str, str]]] = {
    "capacity": (429, False, {}),
    "exhausted_error": (500, False, {}),
    "exhausted_keyless": (None, True, {}),
    "exhausted_keyed": (
        None,
        True,
        {"x-api-key": "sk-ant-test", "x-netllm-local-only": "1"},
    ),
    "upstream_client_err": (404, False, {}),
}

_OPENAI_SURFACE_EXPECTED = {
    # mode -> (status, envelope error "type", envelope "code")
    "capacity": (502, "api_error", None),
    "exhausted_error": (502, "api_error", None),
    "exhausted_keyless": (404, "invalid_request_error", "not_found"),
    "exhausted_keyed": (404, "invalid_request_error", "not_found"),
    "upstream_client_err": (404, "invalid_request_error", "not_found"),
}

_MESSAGES_EXPECTED = {
    # mode -> (status, envelope error "type")
    "capacity": (502, "api_error"),
    "exhausted_error": (502, "api_error"),
    # D11: 401 where the OpenAI surfaces answer 404.
    "exhausted_keyless": (401, "authentication_error"),
    # Keyed but nothing to route to: status-less error, route-mapped 502.
    "exhausted_keyed": (502, "api_error"),
    # D11 semantic flip (Phase 3 commit 3c): forwarded, was 502.
    "upstream_client_err": (404, "not_found_error"),
}


def _drive(path: str, mode: str):
    status, unknown_model, headers = _MODES[mode]
    backends = [
        {
            "name": "alpha",
            "api_format": "openai",
            "models": [_served(path)],
            "script": _script(status) if status is not None else [{"behavior": "ok"}],
        }
    ]
    scenario = {
        "backends": backends,
        "routing": {"default_strategy": "failover", "model_aliases": _ALIAS},
    }
    model = _unknown(path) if unknown_model else _requested(path)
    with contract_environment(scenario) as env:
        return env.drive({"path": path, "body": _body(path, model), "headers": headers})


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("mode", sorted(_MODES))
def test_error_taxonomy_status_and_envelope(path: str, mode: str) -> None:
    result = _drive(path, mode)
    assert result.frames is None, (
        f"{path}/{mode}: a terminal failure must be a real HTTP error, "
        "not a 200 + SSE body (F-32)"
    )
    body = result.body
    assert isinstance(body, dict), f"{path}/{mode}: non-JSON body {body!r}"

    if path in MSG_PATHS:
        status, err_type = _MESSAGES_EXPECTED[mode]
        assert result.status == status, f"{path}/{mode}: body={body}"
        assert body["type"] == "error"
        assert set(body["error"]) == {"type", "message"}
        assert body["error"]["type"] == err_type
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    else:
        status, err_type, code = _OPENAI_SURFACE_EXPECTED[mode]
        assert result.status == status, f"{path}/{mode}: body={body}"
        assert set(body) == {"error"}
        assert set(body["error"]) == {"message", "type", "param", "code"}
        assert body["error"]["type"] == err_type
        assert body["error"]["code"] == code
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]


@pytest.mark.parametrize("path", PATHS)
def test_capacity_failure_does_not_trip_the_row_offline(path: str) -> None:
    """The capacity column's other half: classification happens (exactly
    once, in AttemptRecorder.failure) and keeps the row online."""
    scenario = {
        "backends": [
            {
                "name": "alpha",
                "api_format": "openai",
                "models": [_served(path)],
                "script": _script(429),
            }
        ],
        "routing": {
            "default_strategy": "failover",
            "model_aliases": _ALIAS,
            "max_backend_failures": 1,
        },
    }
    with contract_environment(scenario) as env:
        env.drive({"path": path, "body": _body(path, _requested(path)), "headers": {}})
        pool = env.service.pool
        rows = [b for b in pool.backends if "alpha" in b.base_url]
        assert rows, "farm row missing from the pool"
        row = rows[0]
        assert pool.capacity_rejections.get(row.id) == 1, (
            "capacity classification did not run exactly once for this attempt"
        )
        assert row.health.status == "online", (
            "a capacity rejection must never trip the row offline "
            "(max_backend_failures=1 here, so a hard failure would have)"
        )


def test_capacity_classification_has_exactly_one_call_site() -> None:
    """``is_capacity_error`` is reachable only through the recorder.

    Phase 3's structural half: one classification point, so a fix to what
    counts as "full now" cannot land on some paths and miss others.
    """
    import ast
    import pathlib

    src_root = pathlib.Path(__file__).resolve().parents[2] / "packages"
    callers: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "pool.py":
            continue  # the definition itself
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "is_capacity_error":
                    callers.append(f"{path.name}:{node.lineno}")
    assert len(callers) == 1, f"expected one classification site, found {callers}"
