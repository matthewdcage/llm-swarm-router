"""Malformed-payload regression tests for the web dashboard.

Every list the dashboard walks comes off the wire, and the guards were written
as ``x?.field || []`` — which defends against a *missing* field and against
nothing else. A peer, a proxy or a buggy agent that answers
``{"backends": "oops"}`` or ``{"backends": [null]}`` used to take the page's
renderer down, and the router's try/catch replaced the whole body with
"This page failed to render".

These tests substitute the worst shapes with ``page.route()`` and assert the
weaker contract that should always hold: **every page still renders its ``h1``
and never shows the failure fallback.** An empty state, a partial list or a
warning are all acceptable answers; a throw is not.

Derived from the adversarial payload sweep (findings F01-F29); the payload
bodies below are the ones that reproduced.
"""

from __future__ import annotations

import json

import pytest

from .conftest import RunningServer

PAGES = [
    "overview",
    "backends",
    "models",
    "peers",
    "network",
    "routing",
    "cloud",
    "preferences",
    "integrations",
    "logs",
    "doctor",
]

FAILURE_TEXT = "This page failed to render"

# Route globs for the endpoints the dashboard consumes.
STATUS = "**/netllm/v1/status*"
MODELS = "**/v1/models"
SCHEMA = "**/netllm/v1/config/schema"
CONFIG = "**/netllm/v1/config"
DOCTOR = "**/netllm/v1/doctor"
LOGS = "**/netllm/v1/logs*"
TELEMETRY = "**/netllm/v1/telemetry*"
HARNESSES = "**/netllm/v1/harnesses"
LOCAL_PROVIDERS = "**/netllm/v1/local-providers"

_STATUS_BASE = {"hostname": "e2e-host", "backends": [], "peers": []}
_CONFIG_BASE = {
    "agent": {},
    "discovery": {},
    "swarm": {},
    "routing": {},
    "ui": {},
    "cloud": {},
}
_SCHEMA_SECTIONS = ["agent", "discovery", "swarm", "routing", "ui", "cloud"]


def _status(**over: object) -> dict:
    return {**_STATUS_BASE, **over}


def _config(**over: object) -> dict:
    return {**json.loads(json.dumps(_CONFIG_BASE)), **over}


def _schema(fields: object) -> dict:
    """A /config/schema whose every section carries a malformed `fields`."""
    return {"sections": {s: {"fields": fields} for s in _SCHEMA_SECTIONS}}


# (id, route glob, body). One malformed /config/schema breaks six pages at
# once, status.backends/peers up to four including the landing page, and
# /v1/models three — so each shape is walked across all 11 pages.
PAYLOADS: list[tuple[str, str, object]] = [
    ("status-backends-string", STATUS, _status(backends="oops", peers="oops")),
    ("status-backends-number", STATUS, _status(backends=7, peers=7)),
    ("status-backends-null-entries", STATUS, _status(backends=[None, None])),
    ("status-peers-null-entries", STATUS, _status(peers=[None, None])),
    (
        "status-health-models-string",
        STATUS,
        _status(
            backends=[
                {
                    "id": "b",
                    "base_url": "http://x/v1",
                    "local": True,
                    "provider": "ollama",
                    "health": {"status": "online", "models": "a,b,c"},
                }
            ]
        ),
    ),
    ("status-peer-warnings-string", STATUS, _status(peer_warnings="drift!")),
    ("models-data-string", MODELS, {"data": "abc"}),
    ("models-data-number", MODELS, {"data": 5}),
    ("models-data-object", MODELS, {"data": {"a": 1}}),
    ("models-data-null", MODELS, {"data": None}),
    ("models-data-null-entries", MODELS, {"data": [None, None]}),
    ("schema-fields-string", SCHEMA, _schema("nope")),
    ("schema-fields-number", SCHEMA, _schema(7)),
    ("schema-fields-null-entries", SCHEMA, _schema([None])),
    ("schema-field-without-name", SCHEMA, _schema([{"widget": "text"}])),
    (
        "config-provider-urls-string",
        CONFIG,
        _config(discovery={"provider_urls": {"ollama": "notalist"}}),
    ),
    (
        "config-custom-endpoints-string",
        CONFIG,
        _config(
            discovery={
                "providers": [],
                "provider_urls": {},
                "custom_endpoints": "http://x",
            }
        ),
    ),
    (
        "config-routing-backends-string",
        CONFIG,
        _config(routing={"backends": "x", "policies": []}),
    ),
    (
        "config-routing-backends-null-entries",
        CONFIG,
        _config(routing={"backends": [None, None], "policies": []}),
    ),
    (
        "config-routing-policies-null-entries",
        CONFIG,
        _config(routing={"backends": [], "policies": [None]}),
    ),
    ("config-favorites-string", CONFIG, _config(ui={"model_favorites": "abc"})),
    ("doctor-issues-string", DOCTOR, {"ok": True, "issues": "all good"}),
    ("doctor-issues-null-entries", DOCTOR, {"ok": False, "issues": [None, None]}),
    ("logs-tail-string", LOGS, {"tail": "one big log", "exists": True}),
    ("logs-tail-object", LOGS, {"tail": {"a": 1}, "exists": True}),
    ("local-providers-string", LOCAL_PROVIDERS, {"providers": "ollama"}),
    ("local-providers-null-entries", LOCAL_PROVIDERS, {"providers": [None, None]}),
    ("harnesses-null-entries", HARNESSES, {"harnesses": [None]}),
    (
        "telemetry-loaded-models-string",
        TELEMETRY,
        {
            "omlx": {"available": True, "live": {}, "loaded_models": "a,b"},
            "history": {"omlx_pp_tps": "nope"},
        },
    ),
    (
        "telemetry-history-string",
        TELEMETRY,
        {
            "omlx": {"available": True, "live": {}, "loaded_models": "a,b"},
            "history": "nope",
        },
    ),
]


def _substitute(page, glob: str, body: object) -> None:  # noqa: ANN001
    payload = json.dumps(body)
    page.route(
        glob,
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=payload
        ),
    )


@pytest.mark.parametrize(
    ("case_id", "glob", "body"),
    [pytest.param(c, g, b, id=c) for c, g, b in PAYLOADS],
)
def test_every_page_survives_malformed_payload(  # noqa: ANN001, ANN201
    page,
    agent: RunningServer,
    case_id: str,
    glob: str,
    body: object,
) -> None:
    """No malformed body may take a page's renderer down.

    The route is installed before the first navigation so the substitution is
    in place for the initial load as well as every poll after it.
    """
    errors: list[str] = []
    page.on(
        "console",
        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    _substitute(page, glob, body)
    page.goto(f"{agent.base_url}/ui/", wait_until="domcontentloaded")
    page.wait_for_selector("#page-overview h1", timeout=15000)

    for key in PAGES:
        page.click(f'.nav-item[data-page="{key}"]')
        section = page.locator(f"#page-{key}")
        # Something renders: the page's own heading, never the fallback.
        assert section.locator("h1").count() >= 1, (
            f"{case_id}: page '{key}' rendered no h1"
        )
        assert FAILURE_TEXT not in section.inner_text(), (
            f"{case_id}: page '{key}' hit the render fallback"
        )

    render_failures = [e for e in errors if "failed to render" in e]
    assert not render_failures, f"{case_id}: {render_failures}"


def test_log_stream_is_capped(page, agent: RunningServer) -> None:  # noqa: ANN001
    """A hostile /logs tail must not materialise one DOM row per line.

    The server caps its own tail, so this is only reachable from a proxied or
    hostile endpoint — but the page had no cap of its own, and 50 000 lines
    cost ~1.1 s to render and ~1.3 s per filter keystroke.
    """
    lines = [
        f"2026-08-10 14:02:{i % 60:02d} INFO netllm.test: line {i}"
        for i in range(20000)
    ]
    _substitute(page, LOGS, {"tail": lines, "exists": True, "size_bytes": 1024})
    page.goto(f"{agent.base_url}/ui/", wait_until="domcontentloaded")
    page.wait_for_selector("#page-overview h1", timeout=15000)
    page.click('.nav-item[data-page="logs"]')
    # /logs is fetched lazily when the page is first opened.
    page.wait_for_selector("#page-logs .log-line", timeout=15000)

    rendered = page.locator("#page-logs .log-line").count()
    assert 0 < rendered <= 1000, f"log stream rendered {rendered} rows uncapped"
    # The count of what was matched is still reported truthfully.
    assert "of 20000 lines" in page.locator("#page-logs").inner_text()
