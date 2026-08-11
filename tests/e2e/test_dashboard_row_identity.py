"""The erase-on-rename bug, driven through the real dashboard in a browser.

`tests/test_config_row_identity.py` proves the agent side. This proves the
*web client* holds up its end: the row identity has to survive the whole
round trip -- exported by `admin.config_summary`, parked in `state.configDraft`
by a page that never renders it (it is `read_only`), and put back on the wire
by `schemaItemToPatch`, which drops every other read_only field.

That last step is the one worth a browser: dropping `row_id` there is a
one-line change that no Python test can see, and it silently restores a
HIGH-severity data-loss bug. Here the assertion is made against the file the
agent actually wrote.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from netllm_core.models import BackendOverride, NetllmConfig, SourceConfig, save_config

from .conftest import RunningServer, _free_port, _serve, _shutdown

STORED_API_KEY = "sk-web-must-not-lose-me"
STORED_SECRET = "cursor-secret-web"


@pytest.fixture
def agent_config(tmp_path: Path) -> tuple[NetllmConfig, Path]:
    """Overrides conftest's fixture: a config whose rows carry secrets.

    No stub backend is wired in -- these tests never look at /v1/models, and
    a pinned URL that answers nothing is exactly the row a user would be
    correcting a typo on.
    """
    cfg = NetllmConfig()
    cfg.swarm.mdns = False
    cfg.agent.advertise = False
    cfg.agent.hostname = "e2e-identity"
    cfg.routing.backends = [
        BackendOverride(
            row_id="b-e2e",
            base_url="http://10.0.0.5:1234/v1",
            provider="lmstudio",
            api_key=STORED_API_KEY,
            max_concurrency=3,
        )
    ]
    cfg.routing.sources = [
        SourceConfig(row_id="s-e2e", id="cursor", secret=STORED_SECRET)
    ]
    cfg_path = tmp_path / "config.toml"
    save_config(cfg, cfg_path)
    return cfg, cfg_path


@pytest.fixture
def agent(agent_config: tuple[NetllmConfig, Path]) -> Iterator[RunningServer]:
    from netllm_agent.app import create_app

    cfg, cfg_path = agent_config
    running = _serve(create_app(cfg, config_path=cfg_path), _free_port())
    running.config_path = cfg_path  # type: ignore[attr-defined]
    httpx.get(f"{running.base_url}/health", timeout=10).raise_for_status()
    try:
        yield running
    finally:
        _shutdown(running)


def _saved(cfg_path: Path) -> dict:
    return tomllib.loads(cfg_path.read_text(encoding="utf-8"))


def test_the_exported_backend_row_carries_its_identity_but_not_its_key(dash) -> None:  # noqa: ANN001
    """What the browser is actually holding after the page loads."""
    row = dash.evaluate("state.configDraft.routing.backends[0]")
    assert row["row_id"] == "b-e2e"
    assert row["api_key_set"] is True
    assert row["api_key"] == ""
    assert STORED_API_KEY not in dash.content()
    # The two fields the old hand-listed export dropped, so no client could
    # round-trip them even once the merge found the right row.
    assert row["max_concurrency"] == 3
    assert "api_key_env" in row


def test_editing_a_backend_url_in_the_browser_keeps_the_stored_key(
    dash, agent_config: tuple[NetllmConfig, Path]
) -> None:  # noqa: ANN001
    _cfg, cfg_path = agent_config
    dash.evaluate(
        "state.configDraft.routing.backends[0].base_url = "
        "'http://10.0.0.5:1235/v1'; markDirty();"
    )
    dash.evaluate("() => saveConfig()")
    dash.wait_for_timeout(500)

    saved = _saved(cfg_path)["routing"]["backends"]
    assert len(saved) == 1, f"the save duplicated or dropped a row: {saved}"
    assert saved[0]["base_url"] == "http://10.0.0.5:1235/v1", "the edit did not save"
    assert saved[0]["api_key"] == STORED_API_KEY, (
        "the dashboard's save erased the stored API key -- schemaItemToPatch "
        "is dropping row_id, so the agent read the edit as delete+create"
    )
    assert saved[0]["row_id"] == "b-e2e"
    assert saved[0]["max_concurrency"] == 3
    assert not dash.console_errors


def test_renaming_a_source_in_the_browser_keeps_the_stored_secret(
    dash, agent_config: tuple[NetllmConfig, Path]
) -> None:  # noqa: ANN001
    _cfg, cfg_path = agent_config
    dash.evaluate(
        "state.configDraft.routing.sources[0].id = 'cursor-laptop'; markDirty();"
    )
    dash.evaluate("() => saveConfig()")
    dash.wait_for_timeout(500)

    saved = _saved(cfg_path)["routing"]["sources"]
    assert len(saved) == 1, f"the save duplicated or dropped a row: {saved}"
    assert saved[0]["id"] == "cursor-laptop", "the rename did not save"
    assert saved[0]["secret"] == STORED_SECRET, (
        "the dashboard's save erased the source secret"
    )
    assert saved[0]["row_id"] == "s-e2e"
    assert not dash.console_errors


def test_a_row_added_in_the_browser_is_given_an_identity(
    dash, agent_config: tuple[NetllmConfig, Path]
) -> None:  # noqa: ANN001
    """The client cannot mint ids, so a new row must arrive without one.

    This is the other half of "server-assigned": the browser adds a row with
    no `row_id` at all, and the agent is the thing that gives it one.
    """
    _cfg, cfg_path = agent_config
    dash.evaluate(
        "state.configDraft.routing.backends.push("
        "{base_url: 'http://10.0.0.9:1234/v1', provider: 'custom', "
        "enabled: true, local: true}); markDirty();"
    )
    dash.evaluate("() => saveConfig()")
    dash.wait_for_timeout(500)

    saved = _saved(cfg_path)["routing"]["backends"]
    assert len(saved) == 2, f"the new row did not save: {saved}"
    assert saved[0]["row_id"] == "b-e2e"
    assert saved[1]["row_id"], "the agent did not mint an id for the new row"
    assert saved[1]["row_id"] != saved[0]["row_id"]
    assert saved[0]["api_key"] == STORED_API_KEY
    assert not dash.console_errors
