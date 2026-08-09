"""Which auth gate each route applies — held against the PRE-SPLIT app.py.

Phase 5b split ``create_app`` -- 487 lines, inside a 559-line ``app.py`` --
into ``netllm_agent/routes/``.
``tests/contract/routes.json`` proves no route was added or lost. Nothing
proved the far more dangerous half: that each surviving route still applies
the *same* gate. F-59 in this repo was exactly that failure — a read gate that
was a no-op — and it exposed every configured cloud credential to anyone on
the LAN.

The expected mapping is therefore not written here and not read out of the
current tree. ``scripts/generate-route-auth-gates.py`` parses it out of
``app.py`` at the pinned pre-split commit (vendored as a fixture) into
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
(``test_doctor_open_lan.py:162-200`` and ``test_route_layer_hardening.py:41-79``
for the read/inference 401 and local-client cases,
``test_routing_hardening.py:400-424``); gutting ``require_read_access`` to a
bare ``return`` leaves THIS file green and turns 7 tests in those files red,
which is the division of labour on purpose. This test
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
FIXTURE = REPO_ROOT / "tests/contract/fixtures/app-pre-split.py.txt"

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
    # Read the VENDORED baseline, not `git show <ref>`. CI shallow-clones, so
    # the object is absent (exit 128), and `ref` is a squash-merged branch tip
    # that stops being reachable from main once its PR lands. The fixture is
    # byte-identical to that blob and
    # test_the_vendored_baseline_matches_the_pinned_commit proves it, so this
    # assertion is unchanged in strength and no longer depends on git.
    source = FIXTURE.read_text(encoding="utf-8")

    assert "@app.get(" in source and "@app.post(" in source, (
        f"{FIXTURE.name} (pinned {ref[:12]}) does not register routes inline "
        "— it is not a pre-split app.py"
    )
    assert "netllm_agent.routes" not in source, (
        f"{FIXTURE.name} already imports the routes package — the baseline "
        "has been replaced with post-split code and proves nothing"
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
    487 lines and grew one route at a time.
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


def test_the_vendored_baseline_matches_the_pinned_commit() -> None:
    """The fixture must really be the pre-split ``app.py``, not a lookalike.

    The baseline used to be read live via ``git show <sha>:app.py``. That was
    wrong twice: CI shallow-clones so the object is absent (exit 128), and the
    pinned sha is a feature-branch tip that gets SQUASHED on merge — so it
    stops being reachable from ``main`` the moment its own PR lands. A
    baseline that evaporates when it merges is not a baseline.

    So the source is vendored. Provenance is kept checkable rather than
    assumed: the manifest records the git blob hash, this test recomputes it
    from the file, and — whenever the clone is deep enough to still have the
    object — cross-checks it against git itself. Where git cannot answer, the
    test says so out loud instead of passing quietly.
    """
    import hashlib

    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/contract/fixtures/app-pre-split.py.txt"
    manifest = json.loads(
        (root / "tests/contract/route-auth-gates.json").read_text(encoding="utf-8")
    )

    data = fixture.read_bytes()
    computed = hashlib.sha1(  # noqa: S324 - git's own blob format
        f"blob {len(data)}\0".encode() + data
    ).hexdigest()
    assert computed == manifest["source_blob"], (
        f"{fixture.name} does not match the blob recorded in the manifest "
        f"({computed} != {manifest['source_blob']}); the baseline was edited"
    )

    # The pre-split shape is the whole point: routes declared INSIDE
    # create_app. If this fixture were ever replaced with post-split app.py
    # the manifest would go empty and assert nothing.
    text = data.decode("utf-8")
    assert "@app.get(" in text and "def create_app(" in text, (
        "the vendored baseline does not register routes inline — it is not a "
        "pre-split app.py"
    )

    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{manifest['source_commit']}^{{commit}}"],
        cwd=root,
        capture_output=True,
    )
    if probe.returncode != 0:
        pytest.skip(
            f"clone lacks {manifest['source_commit'][:12]} (shallow, or the "
            "branch was squash-merged); blob hash checked, git cross-check "
            "not possible here"
        )
    blob = subprocess.run(
        [
            "git",
            "rev-parse",
            f"{manifest['source_commit']}:packages/netllm-agent/src/netllm_agent/app.py",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert blob.returncode == 0, blob.stderr
    assert blob.stdout.strip() == manifest["source_blob"], (
        "the vendored baseline disagrees with the blob at the pinned commit"
    )
