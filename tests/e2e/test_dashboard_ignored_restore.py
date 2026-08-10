"""Restoring an ignored endpoint without leaving the Backends page.

`discovery.ignored_urls` could only be edited on Network → Ignored endpoints,
so the loop was: ignore an endpoint on Backends, then go to another page to
take it back. The undo now lives where the action happened; Network keeps the
full editor (adding a URL by hand, seeing which entries a pinned backend
overrules).
"""

from __future__ import annotations

import httpx
from playwright.sync_api import expect

STRAY = "http://127.0.0.1:9911/v1"
OTHER = "http://127.0.0.1:9912/v1"


def _show(page, key: str) -> None:  # noqa: ANN001 - playwright types
    page.click(f'.nav-item[data-page="{key}"]')
    page.wait_for_selector(f"#page-{key}.page.active")
    page.wait_for_timeout(200)


def _ignored_section(page):  # noqa: ANN001, ANN202
    return page.locator(
        "#page-backends details.collapsible"
        ':has(.collapsible-label:text-is("Ignored endpoints"))'
    ).first


def _ignore(page, url: str) -> None:  # noqa: ANN001
    """Exactly what the card's Ignore button calls."""
    page.evaluate("(u) => { ignoreDiscoveryUrl(u); render(); }", url)
    page.wait_for_timeout(200)


def _saved_ignored(base_url: str) -> list[str]:
    body = httpx.get(f"{base_url}/netllm/v1/config", timeout=10).json()
    return list(body.get("discovery", {}).get("ignored_urls") or [])


def test_backends_shows_no_ignored_section_when_nothing_is_ignored(dash) -> None:  # noqa: ANN001
    """An empty collapsed section on a page that has never ignored anything is
    pure noise, so it is not rendered at all."""
    _show(dash, "backends")
    assert _ignored_section(dash).count() == 0


def test_ignoring_shows_the_pending_state_and_the_undo_on_the_same_page(dash) -> None:  # noqa: ANN001
    """After Ignore the section appears, force-opened and saying it is unsaved."""
    _show(dash, "backends")
    _ignore(dash, STRAY)

    section = _ignored_section(dash)
    expect(section).to_have_count(1)
    assert section.evaluate("d => d.open") is True, "a fresh ignore must be visible"
    head = section.locator("summary").inner_text()
    assert "1 ignored" in head or "unsaved" in head.lower(), head
    expect(section.get_by_role("button", name="Restore")).to_have_count(1)
    expect(section).to_contain_text(STRAY)


def test_restore_round_trips_through_a_save(dash) -> None:  # noqa: ANN001
    """ignore -> save -> restore -> save -> the endpoint is offered again.

    Both halves go through the real Save button and are read back over HTTP,
    so this pins the config write, not just the draft.
    """
    base = dash.agent_base_url
    _show(dash, "backends")

    _ignore(dash, STRAY)
    dash.click("#btn-save")
    expect(dash.locator("#btn-save")).to_be_disabled()
    assert STRAY in _saved_ignored(base)

    # The section is still there after the save — now quiet, not forced.
    section = _ignored_section(dash)
    expect(section).to_have_count(1)
    section.get_by_role("button", name="Restore").first.click()
    dash.wait_for_timeout(200)

    expect(dash.locator("#btn-save")).to_be_enabled()
    dash.click("#btn-save")
    expect(dash.locator("#btn-save")).to_be_disabled()

    assert STRAY not in _saved_ignored(base)
    # And the section disappears again, because the list is empty.
    _show(dash, "backends")
    assert _ignored_section(dash).count() == 0


def test_restore_removes_only_the_row_it_was_clicked_on(dash) -> None:  # noqa: ANN001
    _show(dash, "backends")
    _ignore(dash, STRAY)
    _ignore(dash, OTHER)

    section = _ignored_section(dash)
    expect(section.get_by_role("button", name="Restore")).to_have_count(2)
    # Restore the first row; the second must survive untouched.
    section.locator(f'.row:has-text("{STRAY}")').get_by_role(
        "button", name="Restore"
    ).first.click()
    dash.wait_for_timeout(200)

    remaining = dash.evaluate("() => ignoredDiscoveryUrls()")
    assert remaining == [OTHER], remaining


def test_the_two_surfaces_agree(dash) -> None:  # noqa: ANN001
    """Network stays the full editor; both read the same draft list.

    Restoring on Backends must drop the row out of Network's editor too — they
    are one list, not two copies.
    """
    _show(dash, "backends")
    _ignore(dash, STRAY)

    _show(dash, "network")
    # Network renders each entry as an editable <input>, so the URL is a value
    # rather than page text.
    values = dash.evaluate(
        """
        () => [...document.querySelectorAll('#section-ignored-endpoints input')]
          .map((i) => i.value)
        """
    )
    assert STRAY in values, values

    _show(dash, "backends")
    _ignored_section(dash).get_by_role("button", name="Restore").first.click()
    dash.wait_for_timeout(200)

    _show(dash, "network")
    assert dash.evaluate("() => ignoredDiscoveryUrls()") == []


def test_an_overruled_entry_says_so_instead_of_offering_a_useless_restore(  # noqa: ANN001
    dash,
) -> None:
    """A URL also pinned in routing.backends keeps the entry but ignores it.

    Mirrors netllm_core.backend_credentials: the explicit override wins. The
    row is labelled rather than hidden, so the precedence rule is visible.
    """
    _show(dash, "backends")
    pinned = dash.evaluate(
        "() => (state.configDraft.routing.backends || [])[0]?.base_url || ''"
    )
    assert pinned, "fixture has no pinned backend"
    _ignore(dash, pinned)

    section = _ignored_section(dash)
    expect(section).to_have_count(1)
    expect(section).to_contain_text("overruled by a pinned backend")


def test_the_restore_loop_logs_no_console_errors(dash) -> None:  # noqa: ANN001
    _show(dash, "backends")
    _ignore(dash, STRAY)
    _ignored_section(dash).get_by_role("button", name="Restore").first.click()
    dash.wait_for_timeout(300)
    assert dash.console_errors == []
