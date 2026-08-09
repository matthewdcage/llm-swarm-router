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


def test_every_known_harness_has_a_connect_guide() -> None:
    """`netllm connect <id>` must not KeyError on a registry-only addition.

    `connect.py` validates the id against `KNOWN_HARNESSES` and then indexes
    a *second*, independent hand-written dict (`_guides`). The two happen to
    agree, so nothing is broken -- but a harness added to the registry alone
    passes validation and then hard-crashes with `KeyError` in the primary
    onboarding command.

    `docs/extending/PROGRAM.md` §16 lists this assertion under "refuse to cut
    at any budget" and its addendum §12 schedules it in Phase 0; it had not
    been written by Phase 8, and `grep -rn "_guides" tests/` returned nothing.
    The identical guard already existed for the icon convention
    (`test_admin_harnesses.py::test_every_known_harness_has_an_icon_file_on_disk`)
    for a failure that is quieter than this one.

    This is a parity assert, not the Axis F registry: `HarnessSpec` (F1) is
    unlanded, so the shadow roster still exists. See
    `docs/extending/06-harness-integration.md`.
    """
    from netllm_cli.commands.connect import _guides

    guides = _guides("http://127.0.0.1:11400/v1", "http://127.0.0.1:11400", "netllm-x")
    assert set(guides) == {h.id for h in KNOWN_HARNESSES}, (
        "connect.py's _guides dict and KNOWN_HARNESSES disagree: "
        f"{sorted(set(guides) ^ {h.id for h in KNOWN_HARNESSES})}. "
        "`netllm connect` would KeyError on the missing id."
    )
