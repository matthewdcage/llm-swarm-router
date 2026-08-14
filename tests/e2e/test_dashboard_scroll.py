"""Scroll position across dashboard re-renders.

`render()` rebuilds the active page on every metrics poll (5s on overview,
peers, backends, models, cloud) and on the logs poll (10s). Clearing the page
subtree resets the main scroll container unless we restore it.
"""

from __future__ import annotations


def _show(page, key: str) -> None:  # noqa: ANN001
    page.click(f'.nav-item[data-page="{key}"]')
    page.wait_for_selector(f"#page-{key}.page.active")
    page.wait_for_timeout(250)


def _scroll_main(page, offset: int = 400) -> int:  # noqa: ANN001
    """Scroll `#page-main` down and return the position that stuck."""
    return page.evaluate(
        """(offset) => {
          const main = document.getElementById('page-main');
          const page = document.querySelector('.page.active');
          if (page) {
            page.style.minHeight = `${main.clientHeight + offset + 200}px`;
          }
          const max = Math.max(0, main.scrollHeight - main.clientHeight);
          main.scrollTop = Math.min(offset, max);
          return main.scrollTop;
        }""",
        offset,
    )


def test_scroll_position_survives_re_render(dash) -> None:  # noqa: ANN001
    """What the 5s poll does must not throw the user back to the top."""
    _show(dash, "backends")
    before = _scroll_main(dash)
    assert before > 0, "backends page should be tall enough to scroll"

    dash.evaluate("render()")
    dash.wait_for_timeout(200)

    after = dash.evaluate("() => document.getElementById('page-main').scrollTop")
    assert after == before


def test_navigating_to_another_page_resets_scroll(dash) -> None:  # noqa: ANN001
    _show(dash, "backends")
    before = _scroll_main(dash)
    assert before > 0

    _show(dash, "overview")
    after = dash.evaluate("() => document.getElementById('page-main').scrollTop")
    assert after == 0
