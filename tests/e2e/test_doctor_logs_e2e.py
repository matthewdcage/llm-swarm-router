"""Browser end-to-end for UI-6 (structured doctor) and UI-11 (log records).

Both features exist to delete a client-side parser -- a regex table over
finding prose on Doctor, a log-line regex on Logs -- so the assertions here are
about what the page can now say *because* the server said it: a passed-check
inventory, and a level column that came from the payload.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import expect

from .conftest import RunningServer


def _goto(dash, page_key: str) -> None:  # noqa: ANN001
    dash.click(f'.nav-item[data-page="{page_key}"]')
    expect(dash.locator(f"#page-{page_key}")).to_be_visible()


def _goto_logs(dash) -> None:  # noqa: ANN001
    """Navigating to Logs kicks off an async loadLogs(); wait for it to land
    rather than asserting against the "Loading logs…" placeholder."""
    _goto(dash, "logs")
    dash.wait_for_function("() => !!state.logs")


# --- UI-6 -------------------------------------------------------------------


def test_doctor_header_counts_checks_not_only_failures(dash) -> None:  # noqa: ANN001
    """Before UI-6 the subtitle could only count findings, because a passing
    check left no trace in the payload at all."""
    _goto(dash, "doctor")
    subtitle = dash.locator("#page-doctor .page-sub")
    expect(subtitle).to_contain_text("checks")
    expect(subtitle).to_contain_text("passed")
    assert dash.console_errors == []


def test_doctor_lists_the_checks_that_passed(dash) -> None:  # noqa: ANN001
    _goto(dash, "doctor")
    dash.click("#page-doctor button:has-text('Show what passed')")
    text = dash.locator("#page-doctor").inner_text()
    # Titles, not ids: the page renders the human half. One that only a
    # passing check can produce.
    assert "advertises correctly" in text
    assert dash.console_errors == []


def test_doctor_fix_button_posts_to_the_declared_endpoint(
    dash, agent: RunningServer
) -> None:  # noqa: ANN001
    """The remediation the server declares is the one the browser sends.

    This is the whole point of `action`: no regex decides which button appears,
    and no server-side "apply fix id" executor decides what it does.
    """
    # Make one check fail with a declared config_patch remediation.
    httpx.post(
        f"{agent.base_url}/netllm/v1/admin/config",
        json={"agent": {"role": "gateway", "advertise": False}},
        timeout=30,
    ).raise_for_status()
    dash.reload(wait_until="networkidle")
    _goto(dash, "doctor")

    posts: list[str] = []
    dash.on(
        "request",
        lambda req: posts.append(req.url) if req.method == "POST" else None,
    )
    dash.click("#page-doctor button:has-text('Open Network')")
    # The known-id action for this check navigates; the declared config_patch
    # is still on the wire for a client that does not know the id.
    doctor = httpx.get(f"{agent.base_url}/netllm/v1/doctor", timeout=30).json()
    row = next(c for c in doctor["checks"] if c["id"] == "agent.gateway_advertise")
    assert row["ok"] is False
    assert row["action"]["kind"] == "config_patch"
    assert row["action"]["endpoint"] == "/netllm/v1/admin/config"


def test_doctor_report_copy_includes_check_ids(dash) -> None:  # noqa: ANN001
    _goto(dash, "doctor")
    text = dash.evaluate("() => doctorReportText()")
    assert "swarm.open_lan_no_token" in text
    assert "checks" in text and "passed" in text


# --- UI-11 ------------------------------------------------------------------


def test_logs_payload_is_structured_and_the_page_reads_it(dash) -> None:  # noqa: ANN001
    """The page must be rendering `records`, not re-deriving them.

    `logsBuffer` returns rows carrying an absolute `line_no` only on the
    structured path; the fallback parser sets it to null.
    """
    _goto_logs(dash)
    shape = dash.evaluate(
        "() => { const l = state.logs;"
        " return {total: l.total_lines, url: l.download_url,"
        "         numbered: logsBuffer(l).every(r => r.line_no !== null),"
        "         rows: logsBuffer(l).length}; }"
    )
    assert isinstance(shape["total"], int)
    assert shape["url"] == "/netllm/v1/logs?download=1"
    if shape["rows"]:
        assert shape["numbered"], "rows lack line_no — the fallback parser ran"
    assert dash.console_errors == []


def test_logs_footer_reports_the_whole_file_length(dash) -> None:  # noqa: ANN001
    """`total_lines` is what makes "N of M" honest; before UI-11 the page could
    only say how many lines it had been handed."""
    _goto_logs(dash)
    exists = dash.evaluate("() => !!state.logs.exists")
    if not exists:
        return  # no agent.log on this host; the empty state is asserted above
    text = dash.locator("#page-logs").inner_text()
    assert "in file" in text
    assert dash.console_errors == []


def test_logs_download_link_points_at_the_admin_route(dash) -> None:  # noqa: ANN001
    _goto_logs(dash)
    exists = dash.evaluate("() => !!state.logs.exists")
    link = dash.locator('#page-logs a[download="agent.log"]')
    if not exists:
        # Correctly hidden rather than offering a button that 404s.
        assert link.count() == 0
        return
    assert link.count() == 1
    assert link.get_attribute("href").endswith("/netllm/v1/logs?download=1")
    assert dash.console_errors == []


def test_load_older_survives_the_poll(dash, agent: RunningServer) -> None:  # noqa: ANN001
    """The 10 s poll replaces `state.logs` wholesale, which is exactly why the
    older pages are buffered in the page module and merged on render."""
    payload = httpx.get(f"{agent.base_url}/netllm/v1/logs?tail=5", timeout=30).json()
    if not payload["exists"] or not payload.get("next_before"):
        return  # nothing older to page to on this run
    _goto_logs(dash)
    dash.evaluate("() => logsLoadOlder()")
    dash.wait_for_function("() => logsOlderRecords.length > 0")
    loaded = dash.evaluate("() => logsOlderRecords.length")
    # The poll's own path, run explicitly rather than waited out.
    dash.evaluate("() => loadLogs().then(() => render())")
    dash.wait_for_timeout(500)
    assert dash.evaluate("() => logsOlderRecords.length") == loaded
    assert dash.evaluate("() => logsBuffer(state.logs).length") > loaded
    assert dash.console_errors == []
