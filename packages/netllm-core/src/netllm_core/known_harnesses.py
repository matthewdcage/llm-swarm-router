"""Static registry of known AI coding harnesses/CLIs.

Code-owned reference data (candidate binary names, install hints) -- not
user config. Nothing here is persisted into config.toml; it exists purely
to power PATH detection (harness_detection.py) and the one-click
registration UX (docs/cli-source-routing-plan.md Phase 4c/4d).

Regenerating this data (a CLI renames its binary, a new harness is added)
is a code change, not a config migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CUSTOM_SENTINEL_ID = "custom"
"""Not a KNOWN_HARNESSES entry -- header/virtual-key wiring only, no
detection. See SourceConfig / source_identity.resolve_source."""


@dataclass(frozen=True)
class KnownHarness:
    id: str
    display_name: str
    # shutil.which() candidates, checked in order -- see harness_detection.py.
    cli_commands: tuple[str, ...] = field(default_factory=tuple)
    # Copyable install command, never executed on the user's behalf
    # (docs/cli-source-routing-plan.md Phase 4c divergence #1 from the
    # buzz.xyz reference, which auto-installs on toggle).
    install_hint: str = ""
    docs_url: str | None = None


KNOWN_HARNESSES: tuple[KnownHarness, ...] = (
    KnownHarness(
        id="claude-code",
        display_name="Claude Code",
        cli_commands=("claude",),
        install_hint="npm install -g @anthropic-ai/claude-code",
        docs_url="https://docs.claude.com/en/docs/claude-code",
    ),
    KnownHarness(
        id="codex",
        display_name="Codex CLI",
        cli_commands=("codex",),
        install_hint="npm install -g @openai/codex",
        docs_url="https://github.com/openai/codex",
    ),
    # cli_commands below are best-effort guesses, not verified against a
    # real install in this environment -- confirm against a real machine
    # before relying on `detected` for these three (see
    # docs/cli-source-routing-plan.md Phase 4d execution-order gate).
    KnownHarness(
        id="gemini-cli",
        display_name="Gemini CLI",
        cli_commands=("gemini",),
        install_hint="npm install -g @google/gemini-cli",
        docs_url="https://github.com/google-gemini/gemini-cli",
    ),
    KnownHarness(
        id="cursor",
        display_name="Cursor",
        cli_commands=("cursor",),
        install_hint="Install Cursor from https://cursor.com/downloads",
        docs_url="https://cursor.com/downloads",
    ),
    KnownHarness(
        id="honcho",
        display_name="Honcho",
        cli_commands=("honcho",),
        install_hint="pip install honcho",
        docs_url=None,
    ),
    KnownHarness(
        id="buzz",
        display_name="Buzz",
        cli_commands=("buzz-agent",),
        install_hint="See the agent-buzz-slack workspace README",
        docs_url=None,
    ),
)

_BY_ID: dict[str, KnownHarness] = {h.id: h for h in KNOWN_HARNESSES}


def get_known_harness(harness_id: str) -> KnownHarness | None:
    return _BY_ID.get(harness_id)
