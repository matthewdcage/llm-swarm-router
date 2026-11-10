"""A local source address is not proof that the caller is ours.

Admin and read gates trust requests from this machine's own addresses. A web
page open in the user's browser also originates from those addresses, so the
gates additionally require that the request is not cross-site (``Origin`` must
match ``Host``) and not DNS-rebound (``Host`` must be an IP literal or one of
this machine's names).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig
from netllm_core.platform import is_browser_safe_local_request

LOCAL = ("127.0.0.1", 50000)


def _safe(host: str, origin: str | None = None, site: str | None = None) -> bool:
    return is_browser_safe_local_request(
        client_host="127.0.0.1",
        host_header=host,
        origin=origin,
        sec_fetch_site=site,
    )


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:11400", "localhost:11400", "[::1]:11400", "10.0.0.9:11400"],
)
def test_non_browser_local_callers_are_trusted(host: str) -> None:
    assert _safe(host)


def test_dashboard_same_origin_post_is_trusted() -> None:
    assert _safe("127.0.0.1:11400", "http://127.0.0.1:11400", "same-origin")


@pytest.mark.parametrize(
    ("host", "origin", "site"),
    [
        # CSRF: another site posts to the loopback agent.
        ("127.0.0.1:11400", "http://evil.example", None),
        ("127.0.0.1:11400", "null", None),
        ("127.0.0.1:11400", None, "cross-site"),
        # Another local web app on a different port is still not us.
        ("localhost:11400", "http://localhost:3000", "same-site"),
        # DNS rebinding: same-origin as far as the browser can tell.
        ("rebind.evil.example:11400", "http://rebind.evil.example:11400", None),
        ("rebind.evil.example:11400", None, None),
        # No Host at all.
        ("", None, None),
    ],
)
def test_forged_browser_requests_are_not_trusted(
    host: str, origin: str | None, site: str | None
) -> None:
    assert not _safe(host, origin, site)


def test_testserver_host_is_only_trusted_from_the_in_process_client() -> None:
    assert not _safe("testserver")
    assert is_browser_safe_local_request(
        client_host="testclient",
        host_header="testserver",
        origin=None,
        sec_fetch_site=None,
    )


def test_cross_site_post_cannot_drain_the_agent() -> None:
    app = create_app(NetllmConfig())
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1:11400") as client:
        forged = client.post(
            "/netllm/v1/admin/drain",
            content='{"draining": true}',
            headers={"Origin": "http://evil.example", "Content-Type": "text/plain"},
        )
        assert forged.status_code == 403
        assert client.get("/netllm/v1/status/quick").json().get("draining") in (
            False,
            None,
        )


def test_rebound_host_cannot_read_config() -> None:
    app = create_app(NetllmConfig())
    with TestClient(app, client=LOCAL, base_url="http://127.0.0.1:11400") as client:
        assert client.get("/netllm/v1/config").status_code == 200
        rebound = client.get(
            "/netllm/v1/config", headers={"Host": "rebind.evil.example:11400"}
        )
        assert rebound.status_code == 403
