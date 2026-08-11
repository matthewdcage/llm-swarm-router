"""The Home masthead — identity, status, and the two strings you take away.

Home is the landing page, so these assert the things that must be true before
anything is scrolled: the page names the product once (and no longer names
itself "Overview"), the version and serving status are real, the endpoint is
the one a client should actually use, and the join command reaches the
clipboard as a single inert shell word with no cluster token in it.

The page *key* is still ``overview`` — ``const PAGES``, ``registerPage()``,
``data-page``/``#hash``, ``id="page-overview"`` and ``DASHBOARD_CONTROLS`` in
``tests/conformance/kit_config_surfaces.py`` all agree on it and the
conformance kit asserts that agreement. Only the label the user reads changed,
which is what ``test_the_landing_page_is_labelled_home`` pins from both ends.
"""

from __future__ import annotations

import json
import shlex

import httpx
from playwright.sync_api import expect

from .conftest import RunningServer
from .test_dashboard_e2e import _clipboard, _unquoted_metachars

MASTHEAD = "#page-overview .panel.masthead"


def _grant_clipboard(dash) -> None:  # noqa: ANN001
    dash.context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=dash.agent_base_url
    )


def _copy_card(dash, label: str):  # noqa: ANN001, ANN201
    """The masthead copy card whose field label is `label`."""
    return dash.locator(f"{MASTHEAD} .masthead-copy").filter(has_text=label)


# ---------------------------------------------------------------- naming


def test_the_landing_page_is_labelled_home_but_keeps_the_overview_key(dash) -> None:  # noqa: ANN001
    """Label-only rename: what the user reads changed, the key did not."""
    expect(dash.locator('.nav-item[data-page="overview"] .nav-label')).to_have_text(
        "Home"
    )
    assert dash.locator("#page-overview").get_attribute("aria-label") == "Home"

    # The four places the key appears still agree with each other.
    assert dash.evaluate("() => PAGES.includes('overview')")
    assert dash.evaluate("() => typeof PAGE_RENDERERS.overview === 'function'")
    dash.click('.nav-item[data-page="overview"]')
    assert dash.evaluate("() => state.page") == "overview"


def test_home_has_exactly_one_h1_and_it_is_not_overview(dash) -> None:  # noqa: ANN001
    """The masthead carries the h1; the old page title is gone.

    Both halves matter. Dropping the "Overview" heading without giving the
    masthead an h1 would leave the landing page with no outline at all, which
    is the regression the heading-order assertions elsewhere exist to catch.
    """
    heading = dash.locator("#page-overview h1")
    expect(heading).to_have_count(1)
    assert heading.inner_text().strip() == "llm-swarm-router"

    # Not merely "the h1 does not say Overview": no heading of any level on the
    # page may, or the rename is only half done.
    headings = dash.evaluate(
        "() => [...document.querySelectorAll('#page-overview h1, #page-overview h2,"
        " #page-overview h3')].map((h) => h.textContent.trim())"
    )
    assert not any(h.startswith("Overview") for h in headings), headings

    # The masthead is the first thing in the page, not a block somewhere down it.
    assert dash.evaluate(
        "() => document.querySelector('#page-overview').firstElementChild"
        ".classList.contains('masthead')"
    )


# ---------------------------------------------------------------- contents


def test_masthead_renders_the_logo_version_status_and_endpoint(dash) -> None:  # noqa: ANN001
    """Every element the front door has to carry, checked against the API."""
    masthead = dash.locator(MASTHEAD)
    expect(masthead).to_have_count(1)

    # Brand mark: the shared brandLogoEl(), so it follows the effective theme.
    logo = masthead.locator(".brand-logo img")
    expect(logo).to_have_count(1)
    assert logo.get_attribute("alt") == "", "the mark is decorative beside the h1"

    version = httpx.get(f"{dash.agent_base_url}/netllm/v1/version", timeout=10).json()
    expect(masthead.locator(".masthead-version")).to_have_text(f"v{version['version']}")

    # Serving status, and it has to be the real one: this fixture has a healthy
    # stub backend wired in, so anything but "Serving" is a wrong colour.
    expect(masthead.locator(".pill")).to_have_text("Serving")
    assert "ok" in (masthead.locator(".pill").get_attribute("class") or "")

    env = httpx.get(f"{dash.agent_base_url}/netllm/v1/client-env", timeout=10).json()
    expected = (env.get("vars") or env)["OPENAI_BASE_URL"]
    expect(_copy_card(dash, "Serving on").locator("code")).to_have_text(expected)

    # Node facts, moved up out of the old bottom-of-page "This node" panel.
    facts = masthead.locator(".masthead-fact").all_inner_texts()
    labels = {f.splitlines()[0] for f in facts}
    assert {"HOST", "ROLE", "LISTEN", "UPTIME"} <= labels, labels
    assert dash.console_errors == []


def test_the_old_this_node_panel_is_gone_from_the_bottom(dash) -> None:  # noqa: ANN001
    """Node information is in the masthead now, not below the fold.

    The check is positional as well as textual: a "This node" panel that was
    merely retitled and left at the bottom would still be the thing the user
    called unintuitive.
    """
    titles = dash.evaluate(
        "() => [...document.querySelectorAll('#page-overview .panel-title')]"
        ".map((t) => t.textContent.trim())"
    )
    assert not any(t.startswith("This node") for t in titles), titles

    positions = dash.evaluate(
        """
        () => {
          const page = document.querySelector('#page-overview');
          const head = page.querySelector('.panel.masthead');
          const mesh = page.querySelector('.mesh-stage');
          return {
            headTop: head.getBoundingClientRect().top,
            headBottom: head.getBoundingClientRect().bottom,
            meshTop: mesh ? mesh.getBoundingClientRect().top : Infinity,
            viewport: window.innerHeight,
          };
        }
        """
    )
    assert positions["headTop"] < positions["meshTop"]
    # The whole identity block fits on the first screen at a normal window.
    assert positions["headBottom"] <= positions["viewport"], positions


def test_node_details_are_folded_but_present(dash) -> None:  # noqa: ANN001
    """The rarely-needed half (agent id, build, host gauges) stays reachable.

    Folded, not deleted: it is a <details>, so find-in-page still opens it.
    """
    details = dash.locator(f"{MASTHEAD} details.collapsible")
    expect(details).to_have_count(1)
    assert details.get_attribute("open") is None, "node details default to closed"
    details.locator("summary").click()
    expect(details.locator(".panel-body")).to_contain_text("Agent ID")


# ---------------------------------------------------------------- copying


def test_join_command_copies_and_is_shell_safe(dash) -> None:  # noqa: ANN001
    """The masthead's join command is one inert command on the clipboard.

    The agent URL is assembled from `agent.hostname` / `listen` — config free
    text that a hostile or merely careless value reaches. This is the same
    injection sink as the Integrations snippets, so it gets the same test.
    """
    _grant_clipboard(dash)
    card = _copy_card(dash, "Join this swarm")
    shown = card.locator("code").inner_text()
    assert shown.startswith("netllm join "), shown

    card.get_by_role("button", name="Copy").click()
    command = _clipboard(dash)
    assert command == shown, "the clipboard and the screen must agree"
    assert len(command.splitlines()) == 1, command
    assert _unquoted_metachars(command) == "", command

    parts = shlex.split(command)
    assert parts[:2] == ["netllm", "join"], parts
    assert parts[2].startswith("http"), parts


def test_join_command_never_renders_the_cluster_token(dash) -> None:  # noqa: ANN001
    """A configured token turns into a placeholder, never into the value.

    `cluster_token` is write-only server-side, so the dashboard has no copy of
    it to leak — this pins that the *placeholder* path is still a single inert
    shell word, which the unquoted `<cluster token>` form was not.
    """
    _grant_clipboard(dash)
    secret = "s3cret-token-value"  # noqa: S105 - deliberately not in the payload
    # markDirty() so the 5s poll's loadCore() leaves the draft alone; without
    # it this races a refresh that would drop cluster_token_set again.
    dash.evaluate(
        "(secret) => { state.configDraft.swarm.cluster_token_set = true;"
        " state.configDraft.swarm._cluster_token = secret; markDirty(); render(); }",
        secret,
    )
    card = _copy_card(dash, "Join this swarm")
    card.get_by_role("button", name="Copy").click()
    command = _clipboard(dash)

    assert secret not in command, command
    assert secret not in dash.locator("#page-overview").inner_text()
    assert "--token" in command, command
    assert _unquoted_metachars(command) == "", command
    assert len(shlex.split(command)) == 5, shlex.split(command)


def test_client_endpoint_copies_the_value_it_shows(dash) -> None:  # noqa: ANN001
    """Home and the sidebar card resolve the endpoint the same way."""
    _grant_clipboard(dash)
    card = _copy_card(dash, "Serving on")
    shown = card.locator("code").inner_text()
    card.get_by_role("button", name="Copy").click()
    assert _clipboard(dash) == shown
    assert dash.locator("#client-endpoint").inner_text() == shown


# ---------------------------------------------------------------- update state


def test_update_control_is_quiet_when_current_and_loud_only_when_not(dash) -> None:  # noqa: ANN001
    """ "Up to date" is the absence of news, so it is muted, not a green badge.

    An available update is the only state that gets a primary button, and that
    button leads to the Preferences card that owns the download link and the
    upgrade command rather than reimplementing either here.
    """
    dash.evaluate(
        "() => { state.updateInfo = {current: '1.0.0', update_available: false};"
        " render(); }"
    )
    control = dash.locator(f"{MASTHEAD} .masthead-update")
    expect(control).to_contain_text("up to date")
    # Muted, and specifically not painted with a status colour.
    quiet = control.locator("span.muted")
    expect(quiet).to_have_count(1)
    assert control.locator(".pill, .text-ok").count() == 0
    expect(control.get_by_role("button")).to_have_text("Check for updates")

    dash.evaluate(
        "() => { state.updateInfo = {current: '1.0.0', latest: '1.2.3',"
        " update_available: true}; render(); }"
    )
    button = dash.locator(f"{MASTHEAD} .masthead-update button")
    expect(button).to_have_text("Update to v1.2.3")
    button.click()
    assert dash.evaluate("() => state.page") == "preferences"

    # A failed check is a failure, not "no update available".
    dash.evaluate("() => navigate('overview')")
    dash.evaluate(
        "() => { state.updateInfo = {current: '1.0.0', update_available: false,"
        " error: 'release feed unreachable'}; render(); }"
    )
    expect(dash.locator(f"{MASTHEAD} .masthead-update .text-warn")).to_have_text(
        "check failed"
    )
    assert dash.console_errors == []


# ---------------------------------------------------------------- degradation


def test_masthead_degrades_when_the_agent_is_unreachable(  # noqa: ANN201
    page,  # noqa: ANN001
    agent: RunningServer,
) -> None:
    """No status, no version: em-dashes and a danger pill, never a guess."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    # Everything the masthead reads, gone. /health still answers, so this is
    # specifically "the agent is up but told us nothing", the state in which a
    # plausible-looking guess would be most convincing and most wrong.
    for pattern in (
        "**/netllm/v1/status*",
        "**/netllm/v1/version*",
        "**/netllm/v1/update/check*",
        "**/netllm/v1/telemetry*",
        "**/netllm/v1/client-env*",
    ):
        page.route(pattern, lambda route: route.abort())
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.wait_for_selector(MASTHEAD, timeout=15000)

    masthead = page.locator(MASTHEAD)
    expect(masthead.locator("h1")).to_have_text("llm-swarm-router")
    expect(masthead.locator(".pill")).to_have_text("Unreachable")
    assert "error" in (masthead.locator(".pill").get_attribute("class") or "")
    expect(masthead.locator(".masthead-version")).to_have_text("version unknown")

    values = page.evaluate(
        "() => [...document.querySelectorAll('#page-overview .masthead-fact .mono')]"
        ".map((v) => v.textContent.trim())"
    )
    assert values and set(values) == {"—"}, values
    assert errors == []


def test_masthead_survives_a_status_payload_of_the_wrong_shape(  # noqa: ANN201
    page,  # noqa: ANN001
    agent: RunningServer,
) -> None:
    """Home is the landing page, so a throw here is the whole dashboard."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    def handler(route) -> None:  # noqa: ANN001 - playwright type
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "hostname": None,
                    "role": 7,
                    "backends": "not-a-list",
                    "peers": [None],
                    "listen_url": "",
                }
            ),
        )

    page.route("**/netllm/v1/status*", handler)
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.wait_for_selector(MASTHEAD, timeout=15000)
    expect(page.locator("#page-overview h1")).to_have_count(1)
    # A wrong-shaped payload must not be reported as a healthy mesh.
    expect(page.locator(f"{MASTHEAD} .pill")).not_to_have_text("Serving")
    assert errors == []
