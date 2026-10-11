from __future__ import annotations

from torrent_indexer_catalog import (
    catalog_by_jackett_id,
    indexer_matches_hub_category,
    load_catalog,
    normalize_hub_category,
    resolve_indexer_ids_for_search,
)


def test_normalize_hub_category() -> None:
    assert normalize_hub_category("TV") == "tv"
    assert normalize_hub_category("bogus") == "all"


def test_tv_includes_anime_and_general_excludes_movies_only() -> None:
    assert indexer_matches_hub_category(["anime", "tv"], "tv")
    assert indexer_matches_hub_category(["general"], "tv")
    assert indexer_matches_hub_category(["movies"], "tv") is False
    assert indexer_matches_hub_category(["movies"], "movies")


def test_games_excludes_general() -> None:
    assert indexer_matches_hub_category(["games"], "games")
    assert indexer_matches_hub_category(["general"], "games") is False
    assert indexer_matches_hub_category(["games", "general"], "games")


def test_resolve_from_live_catalog() -> None:
    catalog = load_catalog()
    by_id = catalog_by_jackett_id(catalog)
    configured = ["1337x", "yts", "nyaasi", "gamestorrents", "unknown-id"]
    tv_ids = resolve_indexer_ids_for_search("tv", configured, catalog)
    assert "1337x" in tv_ids
    assert "nyaasi" in tv_ids
    assert "yts" not in tv_ids
    assert "unknown-id" in tv_ids
    game_ids = resolve_indexer_ids_for_search("games", configured, catalog)
    assert "gamestorrents" in game_ids
    assert "1337x" not in game_ids
