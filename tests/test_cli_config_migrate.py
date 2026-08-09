"""`netllm config migrate` — the rehearsal command for a rolling upgrade.

The point of `--dry-run` is that an operator can see what a version bump will
do to a five-machine mesh before any machine does it. So the tests that matter
are the ones proving it *writes nothing*: not the config, not a backup, not a
permissions change.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import netllm_cli.main as cli_main
from netllm_core.config_migrations import CURRENT_SCHEMA_VERSION
from typer.testing import CliRunner

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY = (
    REPO_ROOT / "tests/fixtures/config-generations/v1-to-v2/before.toml"
).read_text(encoding="utf-8")


def _legacy_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(LEGACY, encoding="utf-8")
    return path


def test_dry_run_reports_the_pending_migration(tmp_path: Path) -> None:
    path = _legacy_config(tmp_path)
    result = runner.invoke(
        cli_main.app, ["config", "migrate", "--dry-run", "--config", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert "schema_version 1" in result.output
    assert f"schema_version {CURRENT_SCHEMA_VERSION}" in result.output
    assert "1 -> 2" in result.output
    assert "dry run — nothing written" in result.output


def test_dry_run_writes_absolutely_nothing(tmp_path: Path) -> None:
    path = _legacy_config(tmp_path)
    before = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns

    result = runner.invoke(
        cli_main.app, ["config", "migrate", "--dry-run", "--config", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == before_mtime
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"], (
        "--dry-run created a file; it must not even take a backup"
    )


def test_applying_migrates_and_leaves_the_backup(tmp_path: Path) -> None:
    path = _legacy_config(tmp_path)
    result = runner.invoke(cli_main.app, ["config", "migrate", "--config", str(path)])
    assert result.exit_code == 0, result.output
    migrated = tomllib.loads(path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == CURRENT_SCHEMA_VERSION
    backup = tmp_path / "config.toml.bak-v1"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == LEGACY
    # Unknown keys still there — this command must not be a way to lose them.
    assert migrated["future_section"] == {"knob": 3}


def test_second_run_is_a_no_op(tmp_path: Path) -> None:
    path = _legacy_config(tmp_path)
    runner.invoke(cli_main.app, ["config", "migrate", "--config", str(path)])
    after_first = path.read_bytes()
    result = runner.invoke(cli_main.app, ["config", "migrate", "--config", str(path)])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output
    assert path.read_bytes() == after_first


def test_json_output_is_machine_readable(tmp_path: Path) -> None:
    path = _legacy_config(tmp_path)
    result = runner.invoke(
        cli_main.app,
        ["config", "migrate", "--dry-run", "--json", "--config", str(path)],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["from_version"] == 1
    assert report["to_version"] == CURRENT_SCHEMA_VERSION
    assert report["written"] is False
    assert [step["from_version"] for step in report["pending"]] == [1]


def test_a_newer_config_is_reported_and_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    future = CURRENT_SCHEMA_VERSION + 5
    original = f"schema_version = {future}\n\n[agent]\nhostname = 'later'\n"
    path.write_text(original, encoding="utf-8")

    result = runner.invoke(cli_main.app, ["config", "migrate", "--config", str(path)])
    assert result.exit_code == 0, result.output
    assert "written by a NEWER netllm" in result.output
    assert path.read_text(encoding="utf-8") == original


def test_a_corrupt_config_is_refused_not_rewritten(tmp_path: Path) -> None:
    """Exit non-zero, say what is wrong, change nothing. `migrate` is not a
    repair tool and must never look like one."""
    path = tmp_path / "config.toml"
    original = "[agent\nhostname = 'x'\n"
    path.write_text(original, encoding="utf-8")

    result = runner.invoke(cli_main.app, ["config", "migrate", "--config", str(path)])
    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("config.toml.bak-*"))


def test_a_missing_config_is_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    result = runner.invoke(
        cli_main.app, ["config", "migrate", "--dry-run", "--config", str(path)]
    )
    assert result.exit_code == 0, result.output
    assert "nothing to migrate" in result.output
    assert not path.exists()
