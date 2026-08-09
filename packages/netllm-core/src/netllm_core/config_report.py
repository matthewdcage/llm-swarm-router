"""Config-health findings shared by `netllm doctor` and the dashboard.

Config models keep keys this build does not model (see
netllm_core.models.ConfigModel) instead of deleting them on the next save.
That is the right default -- silently destroying a newer agent's keys is
worse than carrying them -- but "kept" must not mean "invisible", so the
things worth a human's attention are reported from here. Both doctor
surfaces call the same function, so they cannot drift apart.

Findings are plain ``{"title", "fix"}`` dicts: `netllm_agent.admin`
appends them to its issue list as-is, and `netllm_cli.commands.diagnose`
unpacks them into its ``(title, fix)`` tuples.
"""

from __future__ import annotations

from pathlib import Path

from netllm_core.models import NetllmConfig, unknown_cloud_provider_ids


def unknown_cloud_provider_issues(config: NetllmConfig) -> list[dict[str, str]]:
    """Report `[cloud.providers.<id>]` ids with no CLOUD_PROVIDERS entry.

    Nothing is materialized from such an entry (every consumer skips a
    provider whose spec lookup returns None), so this is advisory: either
    a typo to fix or a newer release's provider that this agent will start
    honoring after an upgrade.
    """
    unknown = unknown_cloud_provider_ids(config)
    if not unknown:
        return []
    joined = ", ".join(unknown)
    plural = "s" if len(unknown) > 1 else ""
    return [
        {
            "title": f"Unrecognized cloud provider entr{'ies' if plural else 'y'} "
            f"in config: {joined}",
            "fix": "This build has no driver for "
            f"{joined} — the entry is preserved but inert. Upgrade netllm if a "
            "newer release added it, or remove the "
            f"[cloud.providers.{unknown[0]}] section if it is a typo.",
        }
    ]


def deprecated_key_issues(config_path: Path | None) -> list[dict[str, str]]:
    """Deprecated keys present in the user's ACTUAL config.toml.

    Takes a path, not a `NetllmConfig`: a validated model carries every field
    at its default, so it cannot answer "did this user write this key". Only
    the file can, which is why this is the one report that reads from disk.

    Unreadable or unparseable file -> no findings. Doctor already reports a
    broken config through the load failure itself, and a second, vaguer
    message about deprecations would only add noise to it.
    """
    import tomllib

    from netllm_core.deprecations import deprecated_keys_in_document

    if config_path is None or not config_path.is_file():
        return []
    try:
        document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []

    issues: list[dict[str, str]] = []
    for entry in deprecated_keys_in_document(document):
        replacement = (
            f"Switch to {entry.replacement}."
            if entry.replacement
            else "There is no replacement — delete the key."
        )
        issues.append(
            {
                "title": (
                    f"Deprecated config key {entry.config_path} "
                    f"(removed in netllm {entry.remove_in})"
                ),
                "fix": (
                    f"Deprecated since {entry.deprecated_in}. {replacement} "
                    f"{entry.notes}"
                ),
            }
        )
    return issues


def schema_version_issues(config: NetllmConfig) -> list[dict[str, str]]:
    """A config stamped newer than this build understands.

    Not an error: it loads, and unknown keys are preserved. But it means this
    machine is behind the one that wrote the file, and on a mesh that is worth
    saying out loud before someone edits it here and wonders what happened.
    """
    from netllm_core.config_migrations import CURRENT_SCHEMA_VERSION

    if config.schema_version <= CURRENT_SCHEMA_VERSION:
        return []
    return [
        {
            "title": (
                f"config.toml is generation {config.schema_version}; this "
                f"netllm understands generation {CURRENT_SCHEMA_VERSION}"
            ),
            "fix": (
                "It was written by a newer netllm. This build loads it "
                "unchanged and preserves keys it does not model, but it "
                "cannot apply migrations it does not have. Upgrade this "
                "machine before making it the one that manages this config."
            ),
        }
    ]
