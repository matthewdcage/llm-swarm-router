"""UI-11: logs as records, with paging and a download path.

`tail[]` stays raw formatter text because the macOS app and older dashboards
read it. Everything else is additive: `records[]` (the same window, parsed
where the format string lives), `total_lines`, and the `before` cursor that
lets the page walk backwards without the 10 s poll clobbering it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netllm_agent.admin import logs_payload, parse_log_line
from netllm_agent.app import create_app
from netllm_core.models import NetllmConfig

STD_LINE = "2026-08-10 14:02:11,123 WARNING netllm_agent.service.core: pool empty"
BARE_LINE = "INFO:     Started server process [4821]"
TRACEBACK_LINE = '  File "/x/y.py", line 12, in handler'


def _cfg(tmp_path: Path, body: str = "") -> NetllmConfig:
    cfg = NetllmConfig()
    cfg.ui.log_dir = str(tmp_path)
    if body:
        (tmp_path / "agent.log").write_text(body, encoding="utf-8")
    return cfg


def _numbered(count: int) -> str:
    return "".join(
        f"2026-08-10 14:00:00 INFO netllm.x: line {i}\n" for i in range(1, count + 1)
    )


# --- parsing ----------------------------------------------------------------


def test_the_agent_format_round_trips_to_four_fields() -> None:
    record = parse_log_line(STD_LINE, 7)
    assert record["line_no"] == 7
    assert record["ts"] == "2026-08-10 14:02:11,123"
    assert record["level"] == "warn"
    assert record["level_label"] == "WARNING"
    assert record["logger"] == "netllm_agent.service.core"
    assert record["message"] == "pool empty"


def test_a_bare_uvicorn_console_line_still_yields_a_level() -> None:
    record = parse_log_line(BARE_LINE, 1)
    assert record["level"] == "info"
    assert record["logger"] is None
    assert record["message"] == "Started server process [4821]"


def test_a_line_that_matches_nothing_is_kept_verbatim() -> None:
    """A stack-trace continuation is exactly the thing that will not match.
    Dropping it would make the page lie about what the agent logged."""
    record = parse_log_line(TRACEBACK_LINE, 3)
    assert record["level"] is None
    assert record["level_label"] is None
    assert record["ts"] is None
    assert record["message"] == TRACEBACK_LINE


@pytest.mark.parametrize(
    ("label", "level"),
    [
        ("CRITICAL", "error"),
        ("FATAL", "error"),
        ("WARNING", "warn"),
        ("DEBUG", "debug"),
    ],
)
def test_python_level_names_normalise_to_the_four_client_levels(
    label: str, level: str
) -> None:
    line = f"2026-08-10 14:02:11 {label} netllm.x: hi"
    assert parse_log_line(line, 1)["level"] == level


# --- payload shape ----------------------------------------------------------


def test_records_and_tail_describe_the_same_window(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(10)), tail=4)
    assert payload["tail"] == [r["raw"] for r in payload["records"]]
    assert len(payload["tail"]) == 4


def test_the_legacy_keys_survive(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(3)), tail=200)
    for key in ("log_dir", "log_file", "exists", "size_bytes", "tail", "truncated"):
        assert key in payload
    assert payload["exists"] is True
    assert all(isinstance(line, str) for line in payload["tail"])
    assert payload["truncated"] is False


def test_truncated_still_means_earlier_lines_were_omitted(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(10)), tail=4)
    assert payload["truncated"] is True
    assert payload["first_line_no"] == 7


def test_total_lines_counts_the_whole_file_not_the_window(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(500)), tail=10)
    assert payload["total_lines"] == 500
    assert len(payload["records"]) == 10


def test_a_final_line_without_a_newline_still_counts(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, "a\nb\nc"), tail=200)
    assert payload["total_lines"] == 3
    assert payload["records"][-1]["message"] == "c"


def test_a_missing_log_file_is_empty_not_an_error(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path), tail=200)
    assert payload["exists"] is False
    assert payload["records"] == []
    assert payload["total_lines"] == 0
    assert payload["next_before"] is None


def test_the_tail_cap_is_still_2000(tmp_path: Path) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(2500)), tail=99999)
    assert len(payload["records"]) == 2000


# --- paging -----------------------------------------------------------------


def test_before_pages_are_disjoint_and_ordered(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _numbered(25))
    first = logs_payload(cfg, tail=10)
    second = logs_payload(cfg, tail=10, before=first["next_before"])
    third = logs_payload(cfg, tail=10, before=second["next_before"])

    assert [r["line_no"] for r in first["records"]] == list(range(16, 26))
    assert [r["line_no"] for r in second["records"]] == list(range(6, 16))
    assert [r["line_no"] for r in third["records"]] == list(range(1, 6))

    seen = [r["line_no"] for p in (third, second, first) for r in p["records"]]
    assert seen == sorted(seen)
    assert len(seen) == len(set(seen)) == 25


def test_the_cursor_runs_out_at_the_start_of_the_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, _numbered(5))
    page = logs_payload(cfg, tail=10)
    assert page["first_line_no"] == 1
    assert page["next_before"] is None
    assert page["truncated"] is False


def test_paging_past_the_start_returns_nothing_rather_than_wrapping(
    tmp_path: Path,
) -> None:
    payload = logs_payload(_cfg(tmp_path, _numbered(5)), tail=10, before=1)
    assert payload["records"] == []
    assert payload["tail"] == []
    assert payload["next_before"] is None


def test_a_growing_file_does_not_renumber_older_lines(tmp_path: Path) -> None:
    """Absolute line numbers are what let the page splice an older page onto a
    newer tail; if appending renumbered them the buffer would double-render."""
    cfg = _cfg(tmp_path, _numbered(10))
    before_append = logs_payload(cfg, tail=5, before=6)
    (tmp_path / "agent.log").write_text(_numbered(20), encoding="utf-8")
    after_append = logs_payload(cfg, tail=5, before=6)
    assert [r["line_no"] for r in before_append["records"]] == [
        r["line_no"] for r in after_append["records"]
    ]
    assert (
        before_append["records"][0]["message"] == after_append["records"][0]["message"]
    )


# --- the route --------------------------------------------------------------


def _client(cfg: NetllmConfig) -> TestClient:
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    return TestClient(create_app(cfg))


def test_the_route_serves_records_and_honours_before(tmp_path: Path) -> None:
    with _client(_cfg(tmp_path, _numbered(30))) as client:
        first = client.get("/netllm/v1/logs?tail=10").json()
        assert first["total_lines"] == 30
        older = client.get(f"/netllm/v1/logs?tail=10&before={first['next_before']}")
        assert [r["line_no"] for r in older.json()["records"]] == list(range(11, 21))


def test_download_streams_the_whole_file_as_an_attachment(tmp_path: Path) -> None:
    with _client(_cfg(tmp_path, _numbered(2500))) as client:
        resp = client.get("/netllm/v1/logs?download=1")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "attachment" in resp.headers["content-disposition"]
    assert "agent.log" in resp.headers["content-disposition"]
    # The whole file, not the 2000-line tail cap: that is the point of it.
    assert resp.text.count("\n") == 2500


def test_download_without_a_log_file_is_a_404_not_an_empty_body(
    tmp_path: Path,
) -> None:
    with _client(_cfg(tmp_path)) as client:
        assert client.get("/netllm/v1/logs?download=1").status_code == 404


def test_download_is_admin_gated_like_the_tail(tmp_path: Path) -> None:
    """It is the unredacted log -- everything the agent wrote, secrets and all
    -- so it must not be reachable from the LAN without the cluster token."""
    cfg = _cfg(tmp_path, _numbered(3))
    cfg.agent.listen = "0.0.0.0:11400"
    cfg.swarm.cluster_token = "cluster-tok"
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    with TestClient(create_app(cfg), client=("10.9.9.9", 5555)) as client:
        assert client.get("/netllm/v1/logs?download=1").status_code == 403
        assert client.get("/netllm/v1/logs").status_code == 403
        ok = client.get(
            "/netllm/v1/logs?download=1",
            headers={"Authorization": "Bearer cluster-tok"},
        )
        assert ok.status_code == 200


def test_the_payload_names_its_own_download_url(tmp_path: Path) -> None:
    """The page must not have to construct the URL, and must be able to hide
    the button on an agent that predates it."""
    with _client(_cfg(tmp_path, _numbered(3))) as client:
        payload = client.get("/netllm/v1/logs").json()
    assert payload["download_url"] == "/netllm/v1/logs?download=1"
