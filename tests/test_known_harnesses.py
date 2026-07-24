"""Tests for the known-harness static registry."""

from __future__ import annotations

from netllm_core.known_harnesses import KNOWN_HARNESSES, get_known_harness


def test_registry_ids_match_phase1_deferred_set() -> None:
    ids = {h.id for h in KNOWN_HARNESSES}
    assert ids == {"claude-code", "codex", "gemini-cli", "cursor", "honcho", "buzz"}


def test_registry_ids_unique() -> None:
    ids = [h.id for h in KNOWN_HARNESSES]
    assert len(ids) == len(set(ids))


def test_every_entry_has_display_name_and_cli_commands() -> None:
    for h in KNOWN_HARNESSES:
        assert h.display_name
        assert h.cli_commands, f"{h.id} has no cli_commands to detect against"


def test_get_known_harness_found() -> None:
    known = get_known_harness("codex")
    assert known is not None
    assert known.id == "codex"
    assert "codex" in known.cli_commands


def test_get_known_harness_unknown_returns_none() -> None:
    assert get_known_harness("does-not-exist") is None
