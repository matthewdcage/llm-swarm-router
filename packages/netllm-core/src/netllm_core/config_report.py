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
