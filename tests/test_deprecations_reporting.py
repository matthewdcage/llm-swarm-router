"""The deprecation clock where a user meets it: the warning and doctor.

The registry and its CI expiry gate live in
`tests/conformance/kit_versioning.py`. This file asserts the two runtime
surfaces, and in particular that they read the user's **file** rather than the
validated model — a `NetllmConfig` carries every field at its default, so a
report driven off the model would tell every user they use a deprecated key.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from netllm_core.config_report import (
    deprecated_key_issues,
    schema_version_issues,
)
from netllm_core.deprecations import (
    CONFIG_KEY_DEPRECATIONS,
    DEPRECATIONS,
    deprecated_keys_in_document,
)
from netllm_core.models import NetllmConfig, load_config, save_config

DEPRECATED_KEY = "routing.require_same_model_for_shard"


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_a_key_present_in_the_document_is_reported() -> None:
    assert [
        entry.config_path
        for entry in deprecated_keys_in_document(
            {"routing": {"require_same_model_for_shard": True}}
        )
    ] == [DEPRECATED_KEY]


def test_presence_not_truthiness() -> None:
    """`= false` is still a use of the key and still has to be deleted."""
    found = deprecated_keys_in_document(
        {"routing": {"require_same_model_for_shard": False}}
    )
    assert [entry.config_path for entry in found] == [DEPRECATED_KEY]


def test_an_absent_key_is_not_reported() -> None:
    assert deprecated_keys_in_document({"routing": {"default_strategy": "auto"}}) == []
    assert deprecated_keys_in_document({}) == []


def test_load_config_warns_naming_the_key_the_release_and_the_remedy(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "config.toml",
        "[routing]\nrequire_same_model_for_shard = false\n",
    )
    with pytest.warns(DeprecationWarning) as caught:
        load_config(path)
    messages = [str(record.message) for record in caught]
    assert any(DEPRECATED_KEY in message for message in messages)
    assert any("0.6.0" in message for message in messages), (
        "the warning must name the release the key goes away in, or it is "
        "just noise the user cannot schedule around"
    )
    assert any(str(path) in message for message in messages), (
        "the warning must name the file — on a mesh there are several"
    )


def test_load_config_is_silent_for_a_config_without_the_key(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.toml", "[routing]\ndefault_strategy = 'auto'\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        load_config(path)


def test_a_freshly_saved_default_config_produces_no_warning(tmp_path: Path) -> None:
    """The self-inflicted case. `save_config` writes every field from
    `model_dump()`, so without pruning, our own writer would put the key back
    and warn about it forever."""
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        load_config(path)


def test_doctor_lists_deprecated_keys_in_the_users_actual_config(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "config.toml",
        "[routing]\nrequire_same_model_for_shard = true\n",
    )
    issues = deprecated_key_issues(path)
    assert len(issues) == 1
    assert DEPRECATED_KEY in issues[0]["title"]
    assert "0.6.0" in issues[0]["title"]
    assert "delete the key" in issues[0]["fix"].lower()


def test_doctor_says_nothing_about_a_config_that_does_not_use_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    save_config(NetllmConfig(), path)
    assert deprecated_key_issues(path) == []


def test_doctor_is_quiet_about_a_missing_or_broken_config(tmp_path: Path) -> None:
    assert deprecated_key_issues(None) == []
    assert deprecated_key_issues(tmp_path / "nope.toml") == []
    broken = _write(tmp_path / "config.toml", "[agent\n")
    assert deprecated_key_issues(broken) == []


def test_doctor_flags_a_config_from_a_newer_netllm() -> None:
    from netllm_core.config_migrations import CURRENT_SCHEMA_VERSION

    assert schema_version_issues(NetllmConfig()) == []
    ahead = NetllmConfig(schema_version=CURRENT_SCHEMA_VERSION + 1)
    issues = schema_version_issues(ahead)
    assert len(issues) == 1
    assert str(CURRENT_SCHEMA_VERSION + 1) in issues[0]["title"]


def test_cli_doctor_surfaces_a_deprecated_key(tmp_path: Path) -> None:
    """Through the real command, not the helper — this is the wiring test."""
    import netllm_cli.main as cli_main
    from typer.testing import CliRunner

    path = _write(
        tmp_path / "config.toml",
        "[agent]\nlisten = '127.0.0.1:11400'\n\n[routing]\n"
        "require_same_model_for_shard = false\n",
    )
    result = CliRunner().invoke(cli_main.app, ["doctor", "--config", str(path)])
    assert DEPRECATED_KEY in result.output, result.output


def test_every_config_key_deprecation_is_in_the_full_registry() -> None:
    assert set(CONFIG_KEY_DEPRECATIONS) <= set(DEPRECATIONS)
    assert all(entry.kind == "config-key" for entry in CONFIG_KEY_DEPRECATIONS)
