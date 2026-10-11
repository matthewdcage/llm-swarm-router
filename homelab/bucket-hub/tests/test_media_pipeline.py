"""Tests for Sonarr/Radarr auto-add payload helpers."""

from media_pipeline import (
    apply_season_monitoring,
    radarr_add_payload_from_lookup,
    sonarr_add_payload_from_lookup,
)


def test_apply_season_monitoring_wanted_only():
    seasons = [
        {"seasonNumber": 0, "monitored": True},
        {"seasonNumber": 1, "monitored": False},
        {"seasonNumber": 2, "monitored": False},
    ]
    out = apply_season_monitoring(seasons, {1})
    assert out[0]["monitored"] is False
    assert out[1]["monitored"] is True
    assert out[2]["monitored"] is False


def test_apply_season_monitoring_defaults_to_season_one():
    seasons = [{"seasonNumber": 1}, {"seasonNumber": 2}]
    out = apply_season_monitoring(seasons, set())
    assert out[0]["monitored"] is True
    assert out[1]["monitored"] is False


def test_sonarr_add_payload_breaking_bad_s01():
    pick = {
        "title": "Breaking Bad",
        "tvdbId": 81189,
        "titleSlug": "breaking-bad",
        "seasons": [
            {"seasonNumber": 0, "monitored": True},
            {"seasonNumber": 1, "monitored": True},
            {"seasonNumber": 2, "monitored": True},
        ],
    }
    body = sonarr_add_payload_from_lookup(
        pick,
        root_folder="/data/tv",
        wanted_seasons={1},
        quality_profile_id=4,
        language_profile_id=1,
        release_kind="season",
    )
    assert body["rootFolderPath"] == "/data/tv"
    assert body["addOptions"]["searchForMissingEpisodes"] is False
    s1 = next(s for s in body["seasons"] if s["seasonNumber"] == 1)
    s2 = next(s for s in body["seasons"] if s["seasonNumber"] == 2)
    assert s1["monitored"] is True
    assert s2["monitored"] is False


def test_radarr_add_payload_no_search():
    pick = {"title": "Dune", "tmdbId": 438631, "year": 2021}
    body = radarr_add_payload_from_lookup(
        pick, root_folder="/data/movies", quality_profile_id=6
    )
    assert body["rootFolderPath"] == "/data/movies"
    assert body["addOptions"]["searchForMovie"] is False
    assert body["monitored"] is False
