"""`netllm drain` CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import netllm_cli.main as cli_main
from netllm_core.models import NetllmConfig, save_config
from typer.testing import CliRunner

runner = CliRunner()


def _cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    return path


class _FakeResponse:
    def __init__(self, json_body: dict, status_code: int = 200) -> None:
        self._json = json_body
        self.status_code = status_code

    def json(self) -> dict:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    last_post: tuple[str, dict] | None = None

    def __init__(self, *a: object, **k: object) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def post(self, url: str, json: dict) -> _FakeResponse:
        _FakeClient.last_post = (url, json)
        return _FakeResponse({"ok": True, "draining": json["draining"]})


def test_drain_on_posts_draining_true(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch.object(cli_main.httpx, "Client", _FakeClient):
        result = runner.invoke(cli_main.app, ["drain", "on", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert "Draining" in result.output
    url, body = _FakeClient.last_post
    assert url.endswith("/netllm/v1/admin/drain")
    assert body == {"draining": True}


def test_drain_off_posts_draining_false(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch.object(cli_main.httpx, "Client", _FakeClient):
        result = runner.invoke(
            cli_main.app, ["drain", "off", "--config", str(cfg_path)]
        )
    assert result.exit_code == 0, result.output
    assert "Rejoined" in result.output
    _url, body = _FakeClient.last_post
    assert body == {"draining": False}


def test_drain_defaults_to_on(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch.object(cli_main.httpx, "Client", _FakeClient):
        result = runner.invoke(cli_main.app, ["drain", "--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    _url, body = _FakeClient.last_post
    assert body == {"draining": True}


def test_drain_rejects_invalid_state(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    result = runner.invoke(
        cli_main.app, ["drain", "sideways", "--config", str(cfg_path)]
    )
    assert result.exit_code != 0


class _UnreachableClient:
    def __init__(self, *a: object, **k: object) -> None:
        pass

    def __enter__(self) -> _UnreachableClient:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def post(self, *a: object, **k: object):
        raise ConnectionError("agent unreachable")


def test_drain_agent_unreachable_fails_cleanly(tmp_path: Path) -> None:
    cfg_path = _cfg_path(tmp_path)
    with patch.object(cli_main.httpx, "Client", _UnreachableClient):
        result = runner.invoke(cli_main.app, ["drain", "on", "--config", str(cfg_path)])
    assert result.exit_code != 0
