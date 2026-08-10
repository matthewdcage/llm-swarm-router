"""Collapsible sections: the shared `collapsiblePanel()` component.

Every accordion on every page comes from one function in dashboard.js, so
these tests exercise the contract once rather than per page:

* it is a real `<details>`/`<summary>`, so keyboard operation, the
  disclosure role/state and find-in-page expansion come from the browser;
* open/closed survives a re-render — `render()` rebuilds the whole page on
  every 5s poll, and a section that shut itself mid-edit would be worse than
  no accordion at all;
* a section holding an unsaved edit, a failing status or an error force-opens
  regardless of the stored state — an accordion must never be the thing
  hiding the problem;
* a closed section still says what is inside it.

The Backends restore loop is here too, because the section it lives in is one
of these.
"""

from __future__ import annotations

import pytest

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _show(page, key: str) -> None:  # noqa: ANN001 - playwright types
    page.click(f'.nav-item[data-page="{key}"]')
    page.wait_for_selector(f"#page-{key}.page.active")
    page.wait_for_timeout(250)


def _section(page, key: str, title: str):  # noqa: ANN001, ANN202
    """The `<details>` on page `key` whose label is exactly `title`."""
    _show(page, key)
    return page.locator(
        f'#page-{key} details.collapsible:has(.collapsible-label:text-is("{title}"))'
    ).first


def _rerender(page) -> None:  # noqa: ANN001
    """What the 5s status poll does to the DOM, without waiting 5s."""
    page.evaluate("render()")
    page.wait_for_timeout(200)


# --------------------------------------------------------------------------
# the component itself
# --------------------------------------------------------------------------


def test_sections_are_native_details_not_hand_rolled(dash) -> None:  # noqa: ANN001
    """`<details>`/`<summary>`, never a div carrying aria-expanded.

    The native element is what gives keyboard operation, the correct
    screen-reader role and state, and find-in-page expansion of a closed
    section. None of that is reproduced by a div with a click handler, and a
    collapsed section Ctrl-F cannot reach is a section the user cannot find.
    """
    found = 0
    for key in ("backends", "routing", "cloud", "integrations", "preferences"):
        _show(dash, key)
        rows = dash.evaluate(
            """
            () => [...document.querySelectorAll('.page.active .collapsible')].map(
              (d) => ({
                tag: d.tagName,
                summaries: d.querySelectorAll(':scope > summary').length,
                heading: !!d.querySelector(':scope > summary > h2'),
                fakes: d.querySelectorAll(':scope > [aria-expanded]').length,
              }))
            """
        )
        assert rows, f"{key} rendered no collapsible section"
        found += len(rows)
        for row in rows:
            assert row["tag"] == "DETAILS", row
            assert row["summaries"] == 1, row
            # A real heading keeps the page outline navigable: panel() uses an
            # h2 for the same reason, and a folded section must not drop out
            # of the outline just because it folds.
            assert row["heading"], row
            assert row["fakes"] == 0, row
    assert found >= 8, f"expected the component to be in real use, saw {found}"
    assert dash.console_errors == []


def test_a_section_toggles_open_and_closed(dash) -> None:  # noqa: ANN001
    section = _section(dash, "backends", "Manual overrides")
    assert section.evaluate("d => d.open") is False
    section.locator("summary").click()
    dash.wait_for_timeout(150)
    assert section.evaluate("d => d.open") is True
    section.locator("summary").click()
    dash.wait_for_timeout(150)
    assert section.evaluate("d => d.open") is False


def test_a_section_opens_from_the_keyboard(dash) -> None:  # noqa: ANN001
    """No key handler of our own: the summary is focusable and Enter opens it."""
    section = _section(dash, "backends", "Manual overrides")
    section.locator("summary").focus()
    dash.keyboard.press("Enter")
    dash.wait_for_timeout(150)
    assert section.evaluate("d => d.open") is True


def test_open_state_survives_a_re_render(dash) -> None:  # noqa: ANN001
    """The one that matters: render() runs on every poll.

    Without persistence a section opened at t+0 would be shut again a few
    seconds later, while the user was reading — or typing into — it.
    """
    section = _section(dash, "backends", "Manual overrides")
    section.locator("summary").click()
    dash.wait_for_timeout(150)
    assert section.evaluate("d => d.open") is True

    _rerender(dash)
    reopened = _section(dash, "backends", "Manual overrides")
    assert reopened.evaluate("d => d.open") is True

    # And the reverse: a section closed by hand stays closed.
    _section(dash, "backends", "Manual overrides").locator("summary").click()
    dash.wait_for_timeout(150)
    _rerender(dash)
    reclosed = _section(dash, "backends", "Manual overrides")
    assert reclosed.evaluate("d => d.open") is False


def test_open_state_survives_leaving_and_returning_to_the_page(dash) -> None:  # noqa: ANN001
    _section(dash, "backends", "Manual overrides").locator("summary").click()
    dash.wait_for_timeout(150)
    _show(dash, "overview")
    back = _section(dash, "backends", "Manual overrides")
    assert back.evaluate("d => d.open") is True


def test_open_state_survives_a_reload(dash) -> None:  # noqa: ANN001
    """Persisted in localStorage, the same way the theme choice is."""
    _section(dash, "backends", "Manual overrides").locator("summary").click()
    dash.wait_for_timeout(150)
    stored = dash.evaluate("() => window.localStorage.getItem('netllm.sections')")
    assert stored and "backends.overrides" in stored

    dash.reload(wait_until="networkidle")
    dash.wait_for_timeout(400)
    after = _section(dash, "backends", "Manual overrides")
    assert after.evaluate("d => d.open") is True


def test_a_closed_section_still_says_what_is_inside_it(dash) -> None:  # noqa: ANN001
    """A closed section that says nothing is worse than an open one."""
    empty: list[str] = []
    for key in ("backends", "routing", "cloud", "integrations", "preferences"):
        _show(dash, key)
        rows = dash.evaluate(
            """
            () => [...document.querySelectorAll('.page.active details.collapsible')]
              .filter((d) => !d.open)
              .map((d) => ({
                label: (d.querySelector('.collapsible-label') || {}).textContent || '',
                meta: (d.querySelector('.collapsible-meta') || {}).textContent || '',
              }))
            """
        )
        for row in rows:
            if not row["meta"].strip():
                empty.append(f"{key}: {row['label']}")
    assert not empty, f"closed sections with no summary: {empty}"


def test_the_closed_summary_is_in_the_accessible_name(dash) -> None:  # noqa: ANN001
    """A screen-reader user hears the count before deciding to expand.

    The summary text lives inside the heading, which is the summary's only
    child, so it is part of the disclosure's accessible name rather than
    decoration sitting beside it.
    """
    section = _section(dash, "backends", "Manual overrides")
    name = section.locator("summary").inner_text()
    assert "Manual overrides" in name
    assert "pinned" in name or "nothing pinned" in name


def test_the_summary_is_hidden_once_the_section_is_open(dash) -> None:  # noqa: ANN001
    """It is a *closed-state* summary; open, the body says it in full."""
    section = _section(dash, "backends", "Manual overrides")
    meta = section.locator(".collapsible-meta")
    assert meta.is_visible()
    section.locator("summary").click()
    dash.wait_for_timeout(150)
    assert not meta.is_visible()


def test_no_interactive_control_sits_inside_a_summary(dash) -> None:  # noqa: ANN001
    """A checkbox or button inside <summary> is toggled by the fold click.

    There is no way to have one without the other, so the enable switches live
    in the body and the header states enabled/disabled in words. This pins the
    rule so a later card cannot quietly put a control back in the header.
    """
    offenders: list[dict] = []
    for key in ("backends", "routing", "cloud", "integrations", "preferences", "peers"):
        _show(dash, key)
        offenders += dash.evaluate(
            """
            () => {
              const CONTROLS = 'input, button, select, textarea, a[href]';
              const heads = document.querySelectorAll(
                '.page.active details.collapsible > summary');
              return [...heads].flatMap((s) =>
                [...s.querySelectorAll(CONTROLS)].map((c) => ({
                  summary: s.innerText.slice(0, 40), control: c.tagName})));
            }
            """
        )
    assert not offenders, offenders


# --------------------------------------------------------------------------
# force-open: an accordion may never hide state the user needs
# --------------------------------------------------------------------------


def test_an_edited_section_force_opens_over_a_stored_closed_state(dash) -> None:  # noqa: ANN001
    """Close it, edit the field it holds, re-render: it must come back open.

    This is the defect the component exists to make impossible — a pinned
    backend edited into an unsaveable state, folded away behind a closed
    triangle by the next poll.
    """
    section = _section(dash, "routing", "Backend overrides")
    assert section.evaluate("d => d.open") is False

    dash.evaluate(
        """
        () => {
          state.configDraft.routing.backends = [
            ...(state.configDraft.routing.backends || []),
            {base_url: 'http://127.0.0.1:9/v1', provider: 'custom', enabled: true},
          ];
          markDirty();
          render();
        }
        """
    )
    dash.wait_for_timeout(250)
    section = _section(dash, "routing", "Backend overrides")
    assert section.evaluate("d => d.open") is True
    # And it says why, rather than silently refusing to stay shut.
    assert "unsaved" in section.locator("summary").inner_text().lower()


def test_a_forced_section_does_not_overwrite_the_stored_preference(dash) -> None:  # noqa: ANN001
    """forceOpen is a temporary condition, not a change of mind.

    If it wrote through to storage, a section opened once by a transient edit
    would stay open forever after the save.
    """
    _section(dash, "routing", "Backend overrides")
    dash.evaluate(
        """
        () => {
          state.configDraft.routing.backends = [
            {base_url: 'http://127.0.0.1:9/v1', provider: 'custom', enabled: true},
          ];
          markDirty();
          render();
        }
        """
    )
    dash.wait_for_timeout(250)
    stored = dash.evaluate(
        "() => JSON.parse(window.localStorage.getItem('netllm.sections') || '{}')"
    )
    assert stored.get("routing.backends") is not True


def test_a_cloud_provider_with_a_typed_key_force_opens(dash) -> None:  # noqa: ANN001
    """The card holding an unsaved secret cannot be the one that folds."""
    _show(dash, "cloud")
    pid = dash.evaluate(
        "() => Object.keys(state.configDraft.cloud.providers).find("
        "(p) => !state.configDraft.cloud.providers[p].enabled)"
    )
    assert pid, "no disabled cloud provider to test with"
    dash.evaluate(
        "(pid) => {"
        " state.configDraft.cloud.providers[pid]._pending_api_key = 'sk-test';"
        " markDirty(); render(); }",
        pid,
    )
    dash.wait_for_timeout(250)
    opened = dash.evaluate(
        """
        () => [...document.querySelectorAll('#page-cloud details.collapsible')]
          .filter((d) => d.open)
          .map((d) => (d.querySelector('.collapsible-forced') || {}).textContent || '')
        """
    )
    assert any("unsaved" in text for text in opened), opened


def test_cloud_provider_cards_default_open_only_where_there_is_something_to_see(  # noqa: ANN001
    dash,
) -> None:
    """Six fully expanded cards for typically one enabled provider was the
    three-screen page. Enabled or keyed opens; the rest stay folded."""
    _show(dash, "cloud")
    rows = dash.evaluate(
        """
        () => Object.keys(state.configDraft.cloud.providers).map((pid) => ({
          pid,
          enabled: !!state.configDraft.cloud.providers[pid].enabled,
          keyed: !!(((state.config.cloud || {}).providers || {})[pid]
                    || {}).api_key_set,
        }))
        """
    )
    assert rows, "cloud page listed no providers"
    open_count = dash.evaluate(
        "() => document.querySelectorAll("
        "'#page-cloud details.collapsible[open]').length"
    )
    expected = sum(1 for r in rows if r["enabled"] or r["keyed"])
    assert open_count == expected, (rows, open_count)


# --------------------------------------------------------------------------
# peers: informational panel demoted below the roster and folded
# --------------------------------------------------------------------------


def test_peer_warnings_render_below_the_roster_and_start_closed(dash) -> None:  # noqa: ANN001
    """Three drift notices used to fill half the viewport above the peer list.

    They are informational, not why anyone opens this page.
    """
    _show(dash, "peers")
    dash.evaluate(
        """
        () => {
          state.status.peer_warnings = [
            'peer a: routing strategy differs (local_first vs latency)',
            'peer b: version skew 0.4.1 vs 0.4.2',
            'peer c: heartbeat interval differs',
          ];
          render();
        }
        """
    )
    dash.wait_for_timeout(250)
    geometry = dash.evaluate(
        """
        () => {
          const page = document.querySelector('#page-peers');
          const warn = page.querySelector('details.collapsible');
          const roster = [...page.querySelectorAll('.panel')].find((p) =>
            (p.querySelector('.panel-title') || {}).textContent === 'Mesh roster');
          if (!warn || !roster) return null;
          return {
            open: warn.open,
            summary: (warn.querySelector('.collapsible-meta') || {}).textContent || '',
            warnTop: Math.round(warn.getBoundingClientRect().top),
            rosterTop: Math.round(roster.getBoundingClientRect().top),
          };
        }
        """
    )
    assert geometry is not None, "peers page rendered no warnings section or roster"
    assert geometry["open"] is False
    assert geometry["summary"] == "3 peer warnings detected"
    assert geometry["warnTop"] > geometry["rosterTop"], geometry


def test_peer_warnings_use_the_singular_for_one(dash) -> None:  # noqa: ANN001
    _show(dash, "peers")
    dash.evaluate(
        "() => { state.status.peer_warnings = ['peer a: version skew']; render(); }"
    )
    dash.wait_for_timeout(200)
    meta = dash.locator("#page-peers details.collapsible .collapsible-meta")
    text = meta.inner_text()
    assert text == "1 peer warning detected"


def test_no_warnings_renders_no_section_at_all(dash) -> None:  # noqa: ANN001
    """A collapsed "0 peer warnings detected" is noise, not information."""
    _show(dash, "peers")
    dash.evaluate("() => { state.status.peer_warnings = []; render(); }")
    dash.wait_for_timeout(200)
    assert dash.locator("#page-peers details.collapsible").count() == 0


# --------------------------------------------------------------------------
# both themes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_disclosure_is_visible_in_both_themes(dash, theme: str) -> None:  # noqa: ANN001
    """No hardcoded colour: the triangle is currentColor, the forced badge is
    a token. Both must resolve to something painted in either theme."""
    dash.evaluate("(t) => applyTheme(t)", theme)
    dash.wait_for_timeout(150)
    section = _section(dash, "backends", "Manual overrides")
    style = section.evaluate(
        """
        (d) => {
          const tri = d.querySelector('.disclosure');
          const cs = getComputedStyle(tri);
          return {border: cs.borderLeftColor, width: cs.borderLeftWidth,
                  text: getComputedStyle(
                    d.querySelector('.collapsible-label')).color};
        }
        """
    )
    assert style["width"] != "0px"
    for value in (style["border"], style["text"]):
        assert value not in ("", "transparent", "rgba(0, 0, 0, 0)"), style
    assert style["border"] == style["text"], (
        "the disclosure triangle should follow the heading colour (currentColor)"
    )
