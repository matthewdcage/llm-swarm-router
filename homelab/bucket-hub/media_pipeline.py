"""Pure helpers for Sonarr/Radarr auto-add payloads (tested without hub-worker)."""

from __future__ import annotations

from typing import Any


def apply_season_monitoring(
    lookup_seasons: list[dict[str, Any]],
    wanted: set[int],
    *,
    monitor_all_if_empty: bool = False,
) -> list[dict[str, Any]]:
    """Return Sonarr season rows with monitored flags for Hub import."""
    out: list[dict[str, Any]] = []
    for s in lookup_seasons:
        if not isinstance(s, dict):
            continue
        num = int(s.get("seasonNumber") or 0)
        if num == 0:
            monitored = False
        elif wanted:
            monitored = num in wanted
        elif monitor_all_if_empty:
            monitored = True
        else:
            monitored = num == 1
        row = dict(s)
        row["monitored"] = monitored
        out.append(row)
    return out


def sonarr_add_payload_from_lookup(
    pick: dict[str, Any],
    *,
    root_folder: str,
    wanted_seasons: set[int],
    quality_profile_id: int,
    language_profile_id: int,
    release_kind: str = "",
) -> dict[str, Any]:
    """Build POST /api/v3/series body from a lookup row."""
    rk = (release_kind or "").lower()
    monitor_all = rk in ("complete", "full_show") and not wanted_seasons
    seasons_raw = pick.get("seasons") if isinstance(pick.get("seasons"), list) else []
    seasons = apply_season_monitoring(
        seasons_raw,
        wanted_seasons,
        monitor_all_if_empty=monitor_all,
    )
    series_monitored = any(
        s.get("monitored") for s in seasons if int(s.get("seasonNumber") or 0) > 0
    )
    return {
        "title": pick.get("title"),
        "tvdbId": pick.get("tvdbId"),
        "imdbId": pick.get("imdbId"),
        "titleSlug": pick.get("titleSlug"),
        "images": pick.get("images"),
        "seasons": seasons,
        "seasonFolder": pick.get("seasonFolder", True),
        "monitored": series_monitored,
        "qualityProfileId": quality_profile_id,
        "languageProfileId": language_profile_id,
        "rootFolderPath": root_folder,
        "addOptions": {"searchForMissingEpisodes": False},
    }


def radarr_add_payload_from_lookup(
    pick: dict[str, Any],
    *,
    root_folder: str,
    quality_profile_id: int,
) -> dict[str, Any]:
    """Build POST /api/v3/movie body from a lookup row."""
    return {
        "title": pick.get("title"),
        "tmdbId": pick.get("tmdbId"),
        "imdbId": pick.get("imdbId"),
        "titleSlug": pick.get("titleSlug"),
        "images": pick.get("images"),
        "year": pick.get("year"),
        "monitored": False,
        "qualityProfileId": quality_profile_id,
        "rootFolderPath": root_folder,
        "addOptions": {"searchForMovie": False},
    }
