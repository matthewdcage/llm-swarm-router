"""Cloud credential verification, in a real browser — UI-7a.

The unit tests prove the agent refuses to enable an unverified provider. This
proves the thing a user actually meets: that the Cloud page does not *offer*
the switch until the check has passed, that the row says which specific thing
is wrong, and that a key typed but not saved can be checked without saving it.

These drive the real page against the real agent — the only route stubbed is
the outbound call to the cloud provider, which is not ours to make from CI.
"""

from __future__ import annotations

import json

from netllm_core.models import load_config


def _open_cloud(dash):  # noqa: ANN001, ANN202
    dash.click('.nav-item[data-page="cloud"]')
    pid = dash.evaluate("Object.keys(state.configDraft.cloud.providers)[0]")
    assert pid, "config summary listed no cloud providers — fixture is broken"
    return pid


def _card_text(dash, pid: str) -> str:  # noqa: ANN001
    """The provider's card, opened if the disclosure has it folded."""
    dash.evaluate(
        "(pid) => {"
        "  document.querySelectorAll('#page-cloud details').forEach(d => {"
        "    d.open = true;"
        "  });"
        "}",
        pid,
    )
    return dash.locator("#page-cloud").inner_text()


def test_a_provider_with_no_key_cannot_be_enabled_from_the_page(dash) -> None:  # noqa: ANN001
    """The reported bug: the switch was live, and flipping it bought nothing.

    A disabled switch, not a hidden one — the control has to stay visible so
    the state reads as "a step you have not done" rather than "a feature this
    build does not have".
    """
    pid = _open_cloud(dash)
    text = _card_text(dash, pid)
    assert "No key set" in text, text

    enable_disabled = dash.evaluate(
        "(pid) => {"
        "  const btn = document.getElementById('cloud-verify-' + pid);"
        "  const card = btn.closest('.inset');"
        "  const inputs = [...card.querySelectorAll('.switch input')];"
        "  return inputs.some((i) => i.disabled);"
        "}",
        pid,
    )
    assert enable_disabled, "the Enable switch was live for a provider with no key"
    assert dash.console_errors == [], dash.console_errors


def test_a_rejected_key_says_401_not_something_went_wrong(dash, agent) -> None:  # noqa: ANN001
    """The specific blocker, inline on the row — the Backends page has always
    printed what a probe found, and a credential deserves the same."""
    dash.route(
        "**/netllm/v1/cloud/providers/*/verify",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "provider": "moonshot",
                    "status": "unauthorized",
                    "ok": False,
                    "checked_at": "2026-08-10T00:00:00+00:00",
                    "detail": "HTTP 401.",
                    "blocker": "Key rejected by Moonshot AI (Kimi). HTTP 401.",
                    "persisted": True,
                }
            ),
        ),
    )
    pid = _open_cloud(dash)
    dash.evaluate(
        "(pid) => {"
        "  state.configDraft.cloud.providers[pid]._pending_api_key = 'mk-bad';"
        "}",
        pid,
    )
    dash.evaluate(
        f"() => verifyCloudProvider({json.dumps(pid)}, state.configDraft.cloud)"
    )
    dash.wait_for_function(
        "(pid) => !!(state.cloudVerifyResults[pid] || {}).status", arg=pid
    )
    dash.evaluate("render()")
    text = _card_text(dash, pid)
    assert "Key rejected" in text, text
    assert "401" in text, text
    assert dash.console_errors == [], dash.console_errors


def test_verifying_a_typed_key_does_not_save_it(dash, agent, agent_config) -> None:  # noqa: ANN001
    """The unsaved-key problem, end to end.

    The key goes out in the verify request body and nowhere else: nothing has
    been saved, so config.toml must still hold no key — only the outcome.
    """
    _cfg, cfg_path = agent_config
    sent: list[str] = []

    def capture(route) -> None:  # noqa: ANN001
        body = json.loads(route.request.post_data or "{}")
        sent.append(body.get("api_key", ""))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "status": "ok",
                    "ok": True,
                    "checked_at": "2026-08-10T00:00:00+00:00",
                    "detail": "Key accepted — 2 model(s) listed.",
                    "blocker": "",
                    "persisted": True,
                }
            ),
        )

    dash.route("**/netllm/v1/cloud/providers/*/verify", capture)
    pid = _open_cloud(dash)
    # The card is folded by default for a provider with nothing in it, and a
    # folded <details> hides its inputs from the click.
    _card_text(dash, pid)
    dash.fill(f"#cloud-key-{pid}", "mk-typed-not-saved")
    dash.click(f"#cloud-verify-{pid}")
    dash.wait_for_function(
        "(pid) => (state.cloudVerifyResults[pid] || {}).ok === true", arg=pid
    )

    assert sent == ["mk-typed-not-saved"], sent
    stored = load_config(cfg_path).cloud.providers.get(pid)
    assert stored is None or stored.api_key == "", (
        "verifying a typed key wrote it to config.toml"
    )
    assert "verified" in _card_text(dash, pid).lower()
    assert dash.console_errors == [], dash.console_errors
