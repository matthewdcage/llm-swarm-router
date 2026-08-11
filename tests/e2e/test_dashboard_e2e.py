"""End-to-end browser tests for the redesigned dashboard.

Every test drives a real chromium against a real agent. The rule throughout:
a JS exception in any page renderer is a failure, not a degradation — so
`console_errors` is asserted empty rather than ignored.
"""

from __future__ import annotations

import json
import shlex
import tomllib
from pathlib import Path

import httpx
import pytest
import yaml
from netllm_core.models import NetllmConfig, load_config
from playwright.sync_api import expect

from .conftest import (
    CONFIG_HOSTILE_MODEL_ID,
    HOSTILE_MODEL_ID,
    SHELL_HOSTILE_MODEL_ID,
    RunningServer,
)

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


def test_shell_renders_every_nav_item(dash) -> None:  # noqa: ANN001
    for page_key in PAGES:
        item = dash.locator(f'.nav-item[data-page="{page_key}"]')
        expect(item).to_have_count(1)
    assert dash.console_errors == []


def test_status_badge_reports_running(dash) -> None:  # noqa: ANN001
    badge = dash.locator("#status-badge")
    expect(badge).to_contain_text("Running")
    expect(dash.locator("#agent-status-line")).to_contain_text("e2e-host")


@pytest.mark.parametrize("page_key", PAGES)
def test_each_page_renders_without_js_errors(dash, page_key: str) -> None:  # noqa: ANN001
    dash.click(f'.nav-item[data-page="{page_key}"]')
    section = dash.locator(f"#page-{page_key}")
    expect(section).to_be_visible()
    # A page that rendered has a heading; a page that threw is either empty
    # or shows the router's failure message.
    expect(section.locator("h1")).to_have_count(1)
    assert "failed to render" not in section.inner_text()
    assert dash.console_errors == [], f"{page_key}: {dash.console_errors}"


@pytest.mark.parametrize("page_key", PAGES)
def test_only_one_page_visible_at_a_time(dash, page_key: str) -> None:  # noqa: ANN001
    dash.click(f'.nav-item[data-page="{page_key}"]')
    visible = dash.locator(".page.active")
    expect(visible).to_have_count(1)
    assert visible.get_attribute("id") == f"page-{page_key}"


def test_nav_marks_current_page_for_assistive_tech(dash) -> None:  # noqa: ANN001
    dash.click('.nav-item[data-page="routing"]')
    expect(dash.locator('.nav-item[data-page="routing"]')).to_have_attribute(
        "aria-current", "page"
    )
    expect(dash.locator('.nav-item[aria-current="page"]')).to_have_count(1)


def test_hash_routing_round_trips(dash) -> None:  # noqa: ANN001
    dash.click('.nav-item[data-page="logs"]')
    assert dash.url.endswith("#logs")
    dash.goto(dash.url.replace("#logs", "#doctor"))
    expect(dash.locator("#page-doctor")).to_be_visible()
    assert dash.console_errors == []


def test_deep_link_to_page_on_load(agent: RunningServer, page) -> None:  # noqa: ANN001
    page.goto(f"{agent.base_url}/ui/#network", wait_until="networkidle")
    expect(page.locator("#page-network")).to_be_visible()
    expect(page.locator('.nav-item[data-page="network"]')).to_have_class(
        # active class is applied alongside nav-item
        __import__("re").compile(r"\bactive\b")
    )


def test_sidebar_counts_reflect_real_data(dash) -> None:  # noqa: ANN001
    """Counts must track the live payload.

    Deliberately not an exact number: the agent may materialise extra backend
    rows (cloud providers, discovered locals) beyond the one we seed, and
    pinning the count would make this test fail for reasons unrelated to the
    sidebar. What matters is that a count renders and agrees with the API.
    """
    backends = httpx.get(
        f"{dash.agent_base_url}/netllm/v1/backends", timeout=10
    ).json()["backends"]
    expect(dash.locator("#nav-count-backends")).to_have_text(str(len(backends)))

    models = httpx.get(f"{dash.agent_base_url}/v1/models", timeout=10).json()["data"]
    assert models, "stub backend served no models — fixture is broken"
    expect(dash.locator("#nav-count-models")).to_have_text(str(len(models)))


def test_client_endpoint_card_shows_real_base_url(dash) -> None:  # noqa: ANN001
    endpoint = dash.locator("#client-endpoint")
    text = endpoint.inner_text()
    assert text.startswith("http"), text
    assert "/v1" in text


# ---------------------------------------------------------------- themes


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_both_colour_schemes_render(agent: RunningServer, browser, scheme: str) -> None:  # noqa: ANN001
    context = browser.new_context(color_scheme=scheme)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    for page_key in PAGES:
        page.click(f'.nav-item[data-page="{page_key}"]')
        expect(page.locator(f"#page-{page_key} h1")).to_have_count(1)
    assert errors == []
    context.close()


def test_explicit_theme_override_beats_os_preference(
    agent: RunningServer,
    browser,  # noqa: ANN001
) -> None:
    context = browser.new_context(color_scheme="dark")
    page = context.new_page()
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.evaluate("applyTheme('light')")
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme == "light"
    # The token layer must actually repaint, not just set an attribute.
    bg = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"
    )
    assert bg == "#ececec", bg
    page.evaluate("applyTheme('dark')")
    bg_dark = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"
    )
    assert bg_dark == "#1c1c1e", bg_dark
    context.close()


def test_theme_choice_survives_reload(agent: RunningServer, browser) -> None:  # noqa: ANN001
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    page.evaluate("applyTheme('dark')")
    page.reload(wait_until="networkidle")
    theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
    assert theme == "dark"
    context.close()


@pytest.mark.parametrize("os_scheme", ["light", "dark"])
@pytest.mark.parametrize("override", [None, "light", "dark"])
def test_the_brand_mark_follows_the_effective_theme(  # noqa: ANN001
    agent: RunningServer, browser, os_scheme: str, override: str | None
) -> None:
    """The logo must track the theme actually in force, not the OS preference.

    It was a `<picture>` with `media="(prefers-color-scheme: dark)"`, whose
    media query can only ever see the OS setting. Pinning a theme in
    Preferences therefore left the black mark on a dark background — and the
    reverse. The two off-diagonal cases below are the ones that were broken,
    which is why this is a cross-product and not two cases.
    """
    context = browser.new_context(color_scheme=os_scheme)
    page = context.new_page()
    page.goto(f"{agent.base_url}/ui/", wait_until="networkidle")
    if override:
        page.evaluate(f"applyTheme('{override}')")

    effective = override or os_scheme
    applied = page.evaluate(
        "getComputedStyle(document.querySelector('.sidebar-brand .brand-logo img'))"
        ".filter"
    )
    if effective == "dark":
        assert applied == "invert(1)", f"logo not inverted on {effective}: {applied}"
    else:
        assert applied in ("none", ""), f"logo wrongly inverted on {effective}"
    context.close()


# ---------------------------------------------------------------- config save


def test_config_save_round_trips_to_the_agent(dash) -> None:  # noqa: ANN001
    """Change a config value in the browser, hit Save, read it back over HTTP."""
    save = dash.locator("#btn-save")
    expect(save).to_be_disabled()

    dash.evaluate("state.configDraft.swarm.heartbeat_interval_s = 42; markDirty();")
    expect(save).to_be_enabled()
    save.click()
    expect(dash.locator("#toast")).to_contain_text("saved")
    expect(save).to_be_disabled()

    summary = httpx.get(f"{dash.agent_base_url}/netllm/v1/config", timeout=10).json()
    assert summary["swarm"]["heartbeat_interval_s"] == 42


def test_save_button_starts_disabled_and_tracks_dirty_state(dash) -> None:  # noqa: ANN001
    expect(dash.locator("#btn-save")).to_be_disabled()
    dash.evaluate("markDirty()")
    expect(dash.locator("#btn-save")).to_be_enabled()
    dash.evaluate("markDirty(false)")
    expect(dash.locator("#btn-save")).to_be_disabled()


def test_saving_never_blanks_a_stored_secret(dash) -> None:  # noqa: ANN001
    """The write-only contract: saving without typing a key preserves it."""
    base = dash.agent_base_url
    httpx.post(
        f"{base}/netllm/v1/admin/config",
        json={"swarm": {"cluster_token": "s3cret-token"}},
        timeout=10,
    ).raise_for_status()

    dash.reload(wait_until="networkidle")
    bodies: list[str] = []
    dash.on(
        "request",
        lambda req: (
            bodies.append(req.post_data or "")
            if req.method == "POST" and "admin/config" in req.url
            else None
        ),
    )
    dash.evaluate("state.configDraft.swarm.mdns = false; markDirty();")
    dash.click("#btn-save")
    expect(dash.locator("#btn-save")).to_be_disabled()
    assert bodies, "save POST was not captured"
    assert "cloud" not in bodies[-1], "Network-only save must omit cloud section"

    summary = httpx.get(f"{base}/netllm/v1/config", timeout=10).json()
    assert summary["swarm"]["cluster_token_set"] is True, summary["swarm"]
    assert summary["swarm"]["mdns"] is False


def test_navigating_away_keeps_unsaved_edits(dash) -> None:  # noqa: ANN001
    """Switching pages must not silently discard a pending edit."""
    dash.evaluate("state.configDraft.swarm.heartbeat_interval_s = 77; markDirty();")
    dash.click('.nav-item[data-page="logs"]')
    dash.click('.nav-item[data-page="network"]')
    assert dash.evaluate("state.configDraft.swarm.heartbeat_interval_s") == 77
    expect(dash.locator("#btn-save")).to_be_enabled()


# ---------------------------------------------------------------- safety


def test_hostile_model_id_is_escaped_not_executed(dash) -> None:  # noqa: ANN001
    """A model id containing markup must never become live DOM."""
    dash.click('.nav-item[data-page="models"]')
    dash.wait_for_timeout(200)
    # The literal string is present as text somewhere on the page…
    body = dash.locator("body").inner_text()
    assert HOSTILE_MODEL_ID in body or "evil:7b" in body
    # …but no element was ever created from it.
    assert dash.locator("img[onerror]").count() == 0
    assert dash.evaluate("document.querySelectorAll('img[src=\"x\"]').length") == 0
    assert dash.console_errors == []


SHELL_METACHARS = ";|&`$<>()"


def _unquoted_metachars(text: str) -> str:
    """Shell metacharacters sitting outside any quoted word.

    A POSIX single-quoted word is inert: nothing inside it is a
    metacharacter. So "is this payload executable?" reduces to "did anything
    dangerous end up outside the quotes?". Backslash escapes are honoured
    because shellQuote's `'\\''` idiom relies on them.
    """
    found: list[str] = []
    single = double = False
    chars = iter(range(len(text)))
    for i in chars:
        ch = text[i]
        if not single and ch == "\\":
            next(chars, None)  # the escaped character is literal
        elif not double and ch == "'":
            single = not single
        elif not single and ch == '"':
            double = not double
        elif not single and not double and ch in SHELL_METACHARS:
            found.append(ch)
    return "".join(found)


def _open_integrations_with_model(dash, model_id: str):  # noqa: ANN001, ANN201
    """Integrations page with `model_id` chosen in the Model ID picker."""
    dash.context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=dash.agent_base_url
    )
    dash.click('.nav-item[data-page="integrations"]')
    page = dash.locator("#page-integrations")
    page.locator('select[aria-label="Model ID"]').select_option(model_id)
    return page


def _clipboard(dash) -> str:  # noqa: ANN001
    return dash.evaluate("navigator.clipboard.readText()")


def _assert_inert(command: str, payload: str, lines: int) -> None:
    """The payload must survive as one shell word, not as extra commands."""
    # shlex parses POSIX quoting exactly as a shell does: if the whole hostile
    # id comes back as a single argument, nothing in it was ever a separator.
    assert payload in shlex.split(command), (
        f"payload is not a single shell word in {command!r}: {shlex.split(command)!r}"
    )
    stray = _unquoted_metachars(command)
    assert stray == "", f"unquoted shell metacharacters {stray!r} in {command!r}"
    # A newline is a command separator too, and quoting cannot contain one in a
    # `#` comment — so the snippet must still be exactly as many lines as we wrote.
    assert len(command.splitlines()) == lines, command


def test_copied_shell_commands_quote_a_hostile_model_id(dash) -> None:  # noqa: ANN001
    """A peer-published model id must reach the clipboard quoted, not runnable.

    swarm.py republishes a peer's health.models through /v1/models, so the id
    below is attacker-chosen. Every Integrations copy action hands the user a
    command to paste into a shell; unquoted, this id is three commands.
    """
    page = _open_integrations_with_model(dash, SHELL_HOSTILE_MODEL_ID)

    page.get_by_role("button", name="Copy verify command").click()
    verify = _clipboard(dash)
    assert verify.startswith("./netllm test")
    _assert_inert(verify, SHELL_HOSTILE_MODEL_ID, lines=1)

    page.get_by_role("button", name="Copy all three").click()
    three = _clipboard(dash)
    assert "export OPENAI_BASE_URL=" in three
    _assert_inert(three, SHELL_HOSTILE_MODEL_ID, lines=3)

    # The curl smoke test interpolates the id into a JSON body inside a
    # single-quoted -d argument — the same breakout, one layer deeper.
    dash.get_by_role("button", name="curl / your own app").click()
    page.get_by_role("button", name="Copy", exact=True).first.click()
    smoke = _clipboard(dash)
    assert smoke.startswith("curl -s ")
    assert _unquoted_metachars(smoke) == "", smoke
    assert len(smoke.splitlines()) == 4, smoke
    # Here the id is nested one layer deeper: the -d argument must survive as a
    # single shell word *and* parse as the JSON it claims to be.
    argv = shlex.split(smoke)
    body = json.loads(argv[argv.index("-d") + 1])
    assert body["model"] == SHELL_HOSTILE_MODEL_ID

    assert dash.console_errors == []


def test_copied_config_snippets_escape_a_hostile_model_id(dash) -> None:  # noqa: ANN001
    """The same id must not break out of the TOML/YAML string it lands in."""
    page = _open_integrations_with_model(dash, CONFIG_HOSTILE_MODEL_ID)

    dash.get_by_role("button", name="Codex CLI").click()
    page.get_by_role("button", name="Copy", exact=True).first.click()
    toml_text = _clipboard(dash)
    # Parsing is the assertion: a broken-out id makes this raise, and a
    # correctly escaped one round-trips to the exact string.
    assert tomllib.loads(toml_text)["model"] == CONFIG_HOSTILE_MODEL_ID
    assert "malicious" not in tomllib.loads(toml_text)

    dash.get_by_role("button", name="Hermes Agent").click()
    page.get_by_role("button", name="Copy", exact=True).first.click()
    yaml_text = _clipboard(dash)
    parsed = yaml.safe_load(yaml_text)
    assert parsed["model"]["default"] == CONFIG_HOSTILE_MODEL_ID
    assert "malicious" not in parsed

    assert dash.console_errors == []


def _strip_js_comments(src: str) -> str:
    """Comments mentioning innerHTML are documentation, not a vulnerability."""
    import re

    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_no_page_uses_innerhtml_on_untrusted_data(dash) -> None:  # noqa: ANN001
    """Belt and braces: no shipped page builds DOM from a string.

    Asserted against what the agent actually serves, not the files on disk, so
    a stale packaged asset would be caught too.
    """
    base = dash.agent_base_url
    for path in [
        "/ui/dashboard.js",
        "/ui/schema-form.js",
        "/ui/bootstrap.js",
        *[f"/ui/pages/{p}.js" for p in PAGES],
    ]:
        src = _strip_js_comments(httpx.get(f"{base}{path}", timeout=10).text)
        assert "innerHTML" not in src, f"{path} uses innerHTML"
        assert "insertAdjacentHTML" not in src, f"{path} uses insertAdjacentHTML"
        assert "document.write" not in src, f"{path} uses document.write"


# ---------------------------------------------------------------- degradation


def test_dashboard_renders_against_an_agent_with_nothing_configured(
    empty_agent: RunningServer, page
) -> None:  # noqa: ANN001
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{empty_agent.base_url}/ui/", wait_until="networkidle")
    for page_key in PAGES:
        page.click(f'.nav-item[data-page="{page_key}"]')
        section = page.locator(f"#page-{page_key}")
        expect(section.locator("h1")).to_have_count(1)
        assert "failed to render" not in section.inner_text()
    assert errors == []


def test_dashboard_survives_the_agent_going_away(dash, agent: RunningServer) -> None:  # noqa: ANN001
    """Kill the API mid-session: the UI must warn, not white-screen."""
    dash.route("**/netllm/v1/**", lambda route: route.abort())
    dash.route("**/v1/models", lambda route: route.abort())
    dash.route("**/health", lambda route: route.abort())
    dash.click("#btn-refresh")
    dash.wait_for_timeout(1000)
    # The shell survives…
    expect(dash.locator(".sidebar")).to_be_visible()
    # …and the user is told. Silently serving stale data behind a green
    # "Running" badge is the one outcome this must never produce.
    banner = dash.locator("#global-banner")
    badge = dash.locator("#status-badge")
    assert banner.is_visible() or "Unreachable" in badge.inner_text(), (
        f"no failure surfaced; badge read {badge.inner_text()!r}"
    )
    assert "Running" not in badge.inner_text()


# ------------------------------------------------------- hung agent (no reply)
#
# A *failing* endpoint was always handled (the test above). These cover the
# case that was not: a connection accepted and never answered. `lambda _: None`
# is a route handler that neither fulfils, aborts nor continues — the request
# stays open for the life of the page, exactly like a wedged agent.


def _never_answer(route) -> None:  # noqa: ANN001
    """Accept the request and hold it open forever."""


def test_api_requests_time_out_instead_of_hanging_forever(dash) -> None:  # noqa: ANN001
    """api() must reject on its own deadline, naming the path it gave up on.

    The 5 s watchdog is the test's, not the dashboard's: without an
    AbortController the promise never settles at all, and the sentinel turns
    that into a bounded failure instead of a hung CI job.
    """
    dash.route("**/netllm/v1/version", _never_answer)
    message = dash.evaluate(
        """() => Promise.race([
             api('/netllm/v1/version', { timeoutMs: 700 })
               .then(() => 'resolved without timing out', (e) => e.message),
             new Promise((r) => setTimeout(() => r('never settled'), 5000)),
           ])"""
    )
    assert "timed out" in message.lower(), message
    assert "/netllm/v1/version" in message, message


def test_a_hung_endpoint_still_paints_the_page(agent: RunningServer, page) -> None:  # noqa: ANN001
    """First paint must not depend on the network.

    The status endpoint never answers here. The old bootstrap awaited
    refresh() before its first navigate(), so the content area stayed 0 bytes
    with no banner and no timeout — the one true white-screen.
    """
    page.route("**/netllm/v1/status*", _never_answer)
    page.goto(f"{agent.base_url}/ui/", wait_until="domcontentloaded")

    expect(page.locator(".sidebar")).to_be_visible()
    expect(page.locator("#page-overview")).to_contain_text(
        "contacting the agent", timeout=10_000
    )
    painted = page.evaluate(
        "document.getElementById('page-overview').childElementCount"
    )
    assert painted > 0, "content area is still an empty rectangle"


# ------------------------------------------------------- malformed payloads


def test_a_malformed_cloud_catalog_does_not_poison_the_page(dash) -> None:  # noqa: ANN001
    """A catalog body with no `models` list must degrade to a message.

    The bad body used to be cached in state.cloudCatalogs *before* validation,
    so the Cloud page threw on this render and on every later one — including
    the poll re-render — until the tab was reloaded.
    """
    dash.route(
        "**/netllm/v1/cloud/providers/*/models",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            # Valid JSON, valid OpenAI list shape, no `models` key.
            body='{"object": "list", "data": [{"id": "gpt-4o"}]}',
        ),
    )
    dash.click('.nav-item[data-page="cloud"]')
    pid = dash.evaluate("Object.keys(state.configDraft.cloud.providers)[0]")
    assert pid, "config summary listed no cloud providers — fixture is broken"

    dash.evaluate(f"() => fetchCloudCatalog({json.dumps(pid)})")
    section = dash.locator("#page-cloud")
    assert "failed to render" not in section.inner_text()
    # The poll re-render is the second half of the bug: the poisoned cache
    # kept throwing long after the fetch that caused it.
    dash.evaluate("render()")
    assert "failed to render" not in section.inner_text()
    assert dash.console_errors == [], dash.console_errors


# ------------------------------------------------------- config integrity

# Two rows that normalizeDiscoveryUrl() maps to the same key: it appends "/v1"
# to anything that lacks it.
COLLIDING_BACKENDS = [
    {
        "base_url": "http://127.0.0.1:9099",
        "provider": "custom",
        "enabled": True,
        "local": True,
        "api_key": "KEY-A",
    },
    {
        "base_url": "http://127.0.0.1:9099/v1",
        "provider": "custom",
        "enabled": True,
        "local": True,
        "api_key": "KEY-B",
    },
]


def test_an_unrelated_save_keeps_both_backends_and_both_keys(
    dash,  # noqa: ANN001
    agent_config: tuple[NetllmConfig, Path],
) -> None:
    """Saving anything must not merge two backends into one.

    applyDiscoveryCredentialPatch used to rebuild routing.backends from a Map
    keyed by the normalised base_url on *every* save, whether or not a key had
    been typed — so a backend row and its API key were destroyed by a save the
    user believed to be unrelated. routing.backends is sent as a full
    replacement, so a dropped row is dropped on disk.
    """
    _cfg, cfg_path = agent_config
    base = dash.agent_base_url
    httpx.post(
        f"{base}/netllm/v1/admin/config",
        json={"routing": {"backends": COLLIDING_BACKENDS}},
        timeout=10,
    ).raise_for_status()

    dash.reload(wait_until="networkidle")
    assert dash.evaluate("state.configDraft.routing.backends.length") == 2

    # An edit that has nothing to do with backends.
    dash.evaluate("state.configDraft.swarm.heartbeat_interval_s = 43; markDirty();")
    _save_and_wait(dash)

    rows = load_config(cfg_path).routing.backends
    assert [b.base_url for b in rows] == [b["base_url"] for b in COLLIDING_BACKENDS], [
        b.base_url for b in rows
    ]
    assert sorted(b.api_key for b in rows) == ["KEY-A", "KEY-B"], [
        (b.base_url, b.api_key) for b in rows
    ]

    summary = httpx.get(f"{base}/netllm/v1/config", timeout=10).json()
    assert len(summary["routing"]["backends"]) == 2
    assert all(b["api_key_set"] for b in summary["routing"]["backends"])


def _save_and_wait(dash) -> None:  # noqa: ANN001
    """Click Save and block until the POST has actually answered.

    #btn-save is disabled synchronously on click, so waiting on the button
    proves nothing about the round trip.
    """

    def _is_save(response) -> bool:  # noqa: ANN001
        return "admin/config" in response.url and response.request.method == "POST"

    with dash.expect_response(_is_save):
        dash.click("#btn-save")
    expect(dash.locator("#toast")).to_contain_text("saved")


def _draft_secret_paths(dash) -> list[str]:  # noqa: ANN001
    """Every scratch/secret key still sitting in state.configDraft."""
    return dash.evaluate(
        """() => {
          const found = [];
          (function walk(node, path, depth) {
            if (!node || typeof node !== "object" || depth > 8) return;
            Object.keys(node).forEach((k) => {
              const here = path ? path + "." + k : k;
              if (k.startsWith("_pending_") || k === "_cluster_token" ||
                  k === "_serverCredentials") {
                found.push(here);
                return;
              }
              walk(node[k], here, depth + 1);
            });
          })(state.configDraft, "", 0);
          return found;
        }"""
    )


def test_typed_secrets_leave_the_draft_after_a_successful_save(dash) -> None:  # noqa: ANN001
    """A typed key/token must not be re-POSTed on every later save.

    The old cleanup covered only cloud.providers.* (and did not take effect at
    all), so plaintext secrets lived in page memory for the whole session and
    a key rotated out-of-band between two saves was silently overwritten by
    the stale one.
    """
    # Off Overview: its 5 s poll refreshes a clean draft from the agent, which
    # would clear the scratch keys for reasons that have nothing to do with the
    # fix under test.
    dash.click('.nav-item[data-page="cloud"]')
    dash.evaluate(
        """() => {
          state.configDraft.discovery.custom_endpoints = ["http://127.0.0.1:9098/v1"];
          state.configDraft.discovery._serverCredentials = {
            "http://127.0.0.1:9098/v1": { provider: "custom",
                                          _pending_api_key: "SERVER-KEY" },
          };
          state.configDraft.swarm._cluster_token = "ROTATED-TOKEN";
          const providers = state.configDraft.cloud.providers;
          providers[Object.keys(providers)[0]]._pending_api_key = "CLOUD-KEY";
          markDirty();
        }"""
    )
    _save_and_wait(dash)
    assert _draft_secret_paths(dash) == [], _draft_secret_paths(dash)

    # …and the wire agrees: a later, unrelated save carries none of them.
    bodies: list[str] = []
    dash.on(
        "request",
        lambda req: (
            bodies.append(req.post_data or "")
            if req.method == "POST" and "admin/config" in req.url
            else None
        ),
    )
    dash.evaluate("state.configDraft.ui.log_dir = '/tmp/netllm-e2e'; markDirty();")
    _save_and_wait(dash)
    assert bodies, "no second save was captured"
    for secret in ("SERVER-KEY", "ROTATED-TOKEN", "CLOUD-KEY"):
        assert secret not in bodies[-1], f"{secret} was re-sent: {bodies[-1]}"
