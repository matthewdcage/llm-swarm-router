"""Which auth gate each route applies — held against the PRE-SPLIT app.py.

Phase 5b split ``create_app``'s 559 inline lines into ``netllm_agent/routes/``.
``tests/contract/routes.json`` proves no route was added or lost. Nothing
proved the far more dangerous half: that each surviving route still applies
the *same* gate. F-59 in this repo was exactly that failure — a read gate that
was a no-op — and it exposed every configured cloud credential to anyone on
the LAN.

The expected mapping is therefore not written here and not read out of the
current tree. ``scripts/generate-route-auth-gates.py`` parses it out of
``app.py`` at the pinned pre-split commit (via ``git show``) into
``tests/contract/route-auth-gates.json``; this module asserts the *running*
app against that file by recording real gate calls.

**Stated limit of the derivation.** The baseline is only as good as its pin.
If someone re-runs the generator against a post-split ref, the baseline
becomes self-referential and this test proves nothing — so
``test_gate_baseline_is_pre_split`` re-reads the pinned commit from git and
asserts that its ``app.py`` really is the inline shape (registers routes
directly, has no ``routes`` package import). Changing the pin is still
possible, but only in a diff that says so out loud.

Second stated limit: this records *which named gate function each route
calls*, not what that function does. A gate whose body was gutted would still
be recorded as applied. The gates' own behaviour is covered elsewhere
(``test_agent.py`` admin-403 tests, ``test_route_layer_hardening.py``,
``test_swarm_token_gates.py``-style read/inference 401 cases); this test
covers the property those tests cannot see: *coverage* across the whole route
table.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_agent.routes.gates import AccessGates
from netllm_core.models import NetllmConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
GATES_MANIFEST = REPO_ROOT / "tests/contract/route-auth-gates.json"
ROUTES_MANIFEST = REPO_ROOT / "tests/contract/routes.json"
APP_PY = REPO_ROOT / "packages/netllm-agent/src/netllm_agent/app.py"

GATE_NAMES = (
    "require_admin_access",
    "require_read_access",
    "require_inference_access",
)

# The /ui StaticFiles mount is not a handler and applies no gate; routes.json
# still covers its presence.
MOUNTS = frozenset({"/ui"})

# Values substituted into path parameters when probing.
PATH_PARAM_VALUES = {"provider_id": "openai"}

# Raised by the recording gates so the handler body never runs: the gate call
# is the only thing under test, and several handlers would otherwise scan
# ports or talk to upstreams.
_PROBE_STATUS = 599


def _manifest() -> dict:
    return json.loads(GATES_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture
def recorded_gates(monkeypatch) -> list[str]:
    calls: list[str] = []

    def _make(name: str):
        def _recorder(self: AccessGates, request) -> None:
            calls.append(name)
            raise HTTPException(status_code=_PROBE_STATUS, detail="gate probe")

        return _recorder

    for name in GATE_NAMES:
        monkeypatch.setattr(AccessGates, name, _make(name))
    return calls


@pytest.fixture
def probe_client() -> TestClient:
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    # No lifespan: this test never wants background tasks or a provider scan.
    return TestClient(create_app(cfg))


def test_gate_manifest_covers_every_registered_route(probe_client: TestClient) -> None:
    """Every route in the app appears in the mapping, and vice versa.

    Without this, a route added outside the manifest would simply never be
    probed and could carry no gate at all.
    """
    framework = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
    live = {
        (route.path, method)
        for route in probe_client.app.routes
        for method in (getattr(route, "methods", None) or [])
        if route.path not in framework and route.path not in MOUNTS
    }
    declared = {(row["path"], row["method"]) for row in _manifest()["routes"]}
    assert live == declared, (
        "route-auth-gates.json is out of step with the registered routes; "
        "regenerate with scripts/generate-route-auth-gates.py and explain any "
        "route that legitimately has no gate"
    )
    # And the same set routes.json guards, minus the mount.
    manifest_routes = json.loads(ROUTES_MANIFEST.read_text(encoding="utf-8"))["routes"]
    from_routes_json = {
        (row["path"], method)
        for row in manifest_routes
        for method in row["methods"]
        if row["path"] not in MOUNTS
    }
    assert declared == from_routes_json


@pytest.mark.parametrize(
    "row",
    _manifest()["routes"],
    ids=lambda row: f"{row['method']} {row['path']}",
)
def test_route_applies_the_pre_split_gate(
    row: dict, probe_client: TestClient, recorded_gates: list[str]
) -> None:
    path = row["path"]
    for name, value in PATH_PARAM_VALUES.items():
        path = path.replace("{" + name + "}", value)

    method = row["method"].lower()
    kwargs: dict = {}
    if method in {"post", "put", "patch"}:
        kwargs["json"] = {}
    getattr(probe_client, method)(path, **kwargs)

    observed = recorded_gates[0] if recorded_gates else None
    assert observed == row["gate"], (
        f"{row['method']} {row['path']} applies {observed!r}; "
        f"app.py at the pre-split commit applied {row['gate']!r}"
    )
    assert len(recorded_gates) <= 1, (
        f"{row['method']} {row['path']} called more than one gate: {recorded_gates}"
    )


def test_gate_baseline_is_pre_split() -> None:
    """The pinned commit must really be the pre-split app.py.

    A baseline regenerated against the refactored tree would make the test
    above assert the app against itself, which proves nothing. This is the
    guard on that; it cannot stop a deliberate re-pin, only make it visible.
    """
    manifest = _manifest()
    ref = manifest["source_commit"]
    try:
        source = subprocess.run(
            ["git", "show", f"{ref}:{manifest['source_path']}"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover
        pytest.fail(f"cannot read the pinned baseline {ref}: {exc}")

    assert "@app.get(" in source and "@app.post(" in source, (
        f"{ref} does not register routes inline — it is not a pre-split app.py"
    )
    assert "netllm_agent.routes" not in source, (
        f"{ref} already imports the routes package — the baseline has been "
        "re-pinned onto the post-split tree and proves nothing"
    )
    for name in GATE_NAMES:
        assert name in source


def _create_app_node() -> ast.FunctionDef:
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "create_app"
    )


def test_create_app_stays_assembly_only() -> None:
    """PROGRAM.md §5 Phase 5b: create_app <= 150 lines, and no routes in it.

    The budget is the anti-reaccumulation gate: the pre-split function was
    486 lines and grew one route at a time.
    """
    node = _create_app_node()
    assert node.end_lineno is not None
    length = node.end_lineno - node.lineno + 1
    assert length <= 150, (
        f"create_app is {length} lines; the Phase 5b budget is 150. "
        "New routes belong in a netllm_agent/routes/ module."
    )

    decorators = [
        dec
        for sub in ast.walk(node)
        if isinstance(sub, ast.AsyncFunctionDef | ast.FunctionDef)
        for dec in sub.decorator_list
        if isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and isinstance(dec.func.value, ast.Name)
        and dec.func.value.id == "app"
    ]
    assert not decorators, (
        "create_app registers routes inline again; move them into "
        "netllm_agent/routes/ and add the registrar to REGISTRARS"
    )
