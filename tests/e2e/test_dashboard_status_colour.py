"""Status colour must reflect actual state.

Green means *actively good*. An off / idle / not-yet-configured state is
neutral. Warn and danger stay for real problems — and, the reverse failure,
a genuinely bad state must never render neutral.

The defects pinned here were all colours computed from something other than
the state they claimed to describe:

* the Cloud hero painted its panel green and put a green dot next to the word
  "off", because the accent ignored `cloud.enabled` entirely;
* the pool detail's Maintenance panel was orange on every page load, because
  the accent was a constant;
* "Check failed" on Preferences was neutral, so a release check that never
  reached the network read as "nothing to do".
"""

from __future__ import annotations

import httpx
import pytest

THEMES = ["light", "dark"]


def _show(page, key: str) -> None:  # noqa: ANN001 - playwright types
    page.click(f'.nav-item[data-page="{key}"]')
    page.wait_for_selector(f"#page-{key}.page.active")
    page.wait_for_timeout(200)


def _hero(page) -> dict:  # noqa: ANN001
    return page.evaluate(
        """
        () => {
          // The hero is the untitled panel the page appends before the rail
          // layout, so it is the first .panel that is not inside .rail-body.
          const panel = [...document.querySelectorAll('#page-cloud .panel')].find(
            (p) => !p.closest('.rail-body'));
          if (!panel) return null;
          const pill = panel.querySelector('.pill');
          return {
            cls: panel.className,
            pillCls: pill ? pill.className : '',
            pillText: pill ? pill.textContent.trim() : '',
            text: panel.innerText,
          };
        }
        """
    )


@pytest.mark.parametrize("theme", THEMES)
def test_cloud_off_is_neutral_not_green(dash, theme: str) -> None:  # noqa: ANN001
    """Master switch off: nothing is armed, so nothing is green."""
    dash.evaluate("(t) => applyTheme(t)", theme)
    _show(dash, "cloud")
    dash.evaluate("() => { state.configDraft.cloud.enabled = false; render(); }")
    dash.wait_for_timeout(200)

    hero = _hero(dash)
    assert hero is not None, "cloud page rendered no hero panel"
    assert "accent-ok" not in hero["cls"], hero
    assert "accent-warn" not in hero["cls"], hero
    assert hero["pillText"] == "off", hero
    assert "ok" not in hero["pillCls"].split(), hero
    assert "neutral" in hero["pillCls"].split(), hero


def test_cloud_on_but_unusable_warns_instead_of_claiming_armed(dash) -> None:  # noqa: ANN001
    """The dangerous state: the user believes they have failover and does not.

    Enabled with no usable credential on any enabled provider cannot fire, so
    it is a warning — not a green tick, and not silence. The exact wording of
    "usable" is the page's to tighten (it is currently "verified against the
    provider"); what is pinned here is that an unusable provider never reads
    as green.
    """
    _show(dash, "cloud")
    dash.evaluate(
        """
        () => {
          state.configDraft.cloud.enabled = true;
          const providers = state.configDraft.cloud.providers;
          Object.values(providers).forEach((p) => {
            p.enabled = false;
            delete p._pending_api_key;
          });
          const summaries = (state.config.cloud || {}).providers || {};
          Object.values(summaries).forEach((s) => {
            s.api_key_set = false;
            delete s.verification;
          });
          const first = Object.keys(providers)[0];
          providers[first].enabled = true;
          state.cloudVerifyResults = {};
          render();
        }
        """
    )
    dash.wait_for_timeout(200)

    hero = _hero(dash)
    assert "warn" in hero["pillCls"].split(), hero
    assert "accent-ok" not in hero["cls"], hero
    assert "accent-warn" in hero["cls"], hero
    assert "cannot" in hero["text"], hero["text"]


def test_cloud_armed_with_a_usable_credential_is_green(dash) -> None:  # noqa: ANN001
    """The third state, which the page previously could not express."""
    _show(dash, "cloud")
    dash.evaluate(
        """
        () => {
          state.configDraft.cloud.enabled = true;
          const first = Object.keys(state.configDraft.cloud.providers)[0];
          state.configDraft.cloud.providers[first].enabled = true;
          const summaries = (state.config.cloud || {}).providers || {};
          summaries[first].api_key_set = true;
          // Whatever the page's current bar for "usable" is, this provider
          // clears it: a stored key and a positive verification record.
          summaries[first].verification = {
            status: 'ok', ok: true, can_enable: true, blocker: '', detail: '',
          };
          render();
        }
        """
    )
    dash.wait_for_timeout(200)

    hero = _hero(dash)
    assert hero["pillText"] == "armed", hero
    assert "ok" in hero["pillCls"].split(), hero
    assert "accent-ok" in hero["cls"], hero


def test_maintenance_is_only_orange_while_actually_draining(dash) -> None:  # noqa: ANN001
    """A destructive button is not a warning state; being drained is."""
    _show(dash, "models")
    dash.evaluate(
        """
        () => {
          state.configDraft.routing.model_pools = {
            'e2e-pool': {hosts: ['peer:aaaa1111'], models: ['gemma4:27b']},
          };
          state.status.draining = false;
          state.openPoolId = 'e2e-pool';
          render();
        }
        """
    )
    dash.wait_for_timeout(250)

    def maintenance() -> dict:
        return dash.evaluate(
            """
            () => {
              const p = [...document.querySelectorAll('#page-models .panel')]
                .find((n) => (n.querySelector('.panel-title') || {})
                  .textContent === 'Maintenance');
              return p
                ? {cls: p.className, pills: p.querySelectorAll('.pill').length}
                : null;
            }
            """
        )

    quiet = maintenance()
    assert quiet is not None, "pool detail rendered no Maintenance panel"
    assert "accent-warn" not in quiet["cls"], quiet

    dash.evaluate("() => { state.status.draining = true; render(); }")
    dash.wait_for_timeout(250)
    loud = maintenance()
    assert "accent-warn" in loud["cls"], loud
    assert loud["pills"] >= 1, loud


def test_a_failed_update_check_is_not_rendered_as_neutral(dash) -> None:  # noqa: ANN001
    """The reverse failure: a real problem shown as an absence of news."""
    _show(dash, "preferences")
    dash.evaluate(
        "() => { state.updateInfo = {error: 'network unreachable', current: '0.4.1'};"
        " render(); }"
    )
    dash.wait_for_timeout(200)
    pill = dash.evaluate(
        """
        () => {
          const p = [...document.querySelectorAll('#page-preferences .panel')].find(
            (n) => (n.querySelector('.panel-title') || {}).textContent === 'Updates');
          const q = p && p.querySelector('.pill');
          return q ? {cls: q.className, text: q.textContent.trim()} : null;
        }
        """
    )
    assert pill is not None, "preferences rendered no Updates pill"
    assert pill["text"] == "Check failed"
    assert "warn" in pill["cls"].split(), pill


def test_an_identity_badge_does_not_claim_a_health_state(dash) -> None:  # noqa: ANN001
    """ "you" marks which row is this agent; the status dot carries health.

    Green next to a red dot said the node was fine and broken at once.
    """
    _show(dash, "peers")
    badges = dash.evaluate(
        """
        () => [...document.querySelectorAll('#page-peers .pill')]
          .filter((p) => p.textContent.trim().startsWith('you'))
          .map((p) => p.className)
        """
    )
    assert badges, "peers page rendered no self row"
    for cls in badges:
        assert "ok" not in cls.split(), cls


def test_no_panel_accent_is_a_hardcoded_constant(dash) -> None:  # noqa: ANN001
    """Source-level guard: an accent must be derived, never a literal.

    A constant `"accent-warn"` argument is the exact shape of the Maintenance
    defect — a colour that cannot ever change with the state it names.
    """
    import re
    from pathlib import Path

    root = (
        Path(__file__).resolve().parents[2]
        / "packages/netllm-agent/src/netllm_agent/static/pages"
    )
    # `panel(root, title, note, "accent-…")` with a string literal in the
    # accent position, on one line.
    literal = re.compile(r'panel\([^;]*?,\s*"accent-(ok|warn|danger)"\s*\)')
    offenders = []
    for path in sorted(root.glob("*.js")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if literal.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    # logs.js "Logs unavailable" is the one honest constant: the panel only
    # exists in the failure branch, so its accent cannot be wrong.
    offenders = [o for o in offenders if "Logs unavailable" not in o]
    assert not offenders, offenders


def test_cloud_state_survives_a_real_save(dash) -> None:  # noqa: ANN001
    """Turning cloud off and saving leaves the hero neutral after a reload."""
    base = dash.agent_base_url
    _show(dash, "cloud")
    dash.evaluate("() => { state.configDraft.cloud.enabled = false; markDirty(); }")
    dash.click("#btn-save")
    dash.wait_for_timeout(400)
    assert (
        httpx.get(f"{base}/netllm/v1/config", timeout=10).json()["cloud"]["enabled"]
        is False
    )

    dash.reload(wait_until="networkidle")
    _show(dash, "cloud")
    hero = _hero(dash)
    assert hero["pillText"] == "off", hero
    assert "accent-ok" not in hero["cls"], hero
    assert dash.console_errors == []
