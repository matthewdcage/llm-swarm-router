from hub_torrent_parse import (
    classify_release_kind,
    format_season_span_label,
    guess_show_title,
    normalize_tv_search_query,
    parse_season_bucket,
    season_numbers_from_title,
    target_seasons_from_query,
    torrent_pack_search_queries,
    torrent_row_matches_target_season,
)


def test_classify_full_show():
    assert classify_release_kind("Better Call Saul S01-S06 1080p x265") == "full_show"
    assert classify_release_kind("The Office Complete Season 1-9 WEB-DL") == "full_show"


def test_partial_range_not_full_show():
    assert classify_release_kind("Better Call Saul - S01-2 [BDRip x264]") == "season"
    assert season_numbers_from_title("Better Call Saul - S01-2 [BDRip x264]") == [1, 2]
    assert format_season_span_label([1, 2]) == "S01–S02"


def test_classify_season_complete():
    assert classify_release_kind("Better.Call.Saul.S05.Complete.1080p") == "season"
    assert classify_release_kind("Better Call Saul S03 1080p AMZN") == "season"


def test_classify_episode():
    assert classify_release_kind("Better Call Saul S01E01 1080p") == "episode"


def test_season_bucket():
    assert parse_season_bucket("Better.Call.Saul.S05.Complete") == "s05"
    assert parse_season_bucket("Better Call Saul S01-S06") == "full_show"
    assert parse_season_bucket("Better Call Saul S01E01") == "episode"


def test_pack_search_queries():
    qs = torrent_pack_search_queries("better call saul")
    assert "better call saul complete" in [q.lower() for q in qs]
    assert any("season" in q.lower() for q in qs)


def test_guess_show_title():
    assert "Better Call Saul" in guess_show_title(
        "Better Call Saul S01E01 1080p WEB-DL x265"
    )


def test_normalize_tv_search_query():
    assert normalize_tv_search_query("breaking bad s1") == "breaking bad season 1"
    assert normalize_tv_search_query("breaking bad s01") == "breaking bad season 1"
    assert normalize_tv_search_query("Better Call Saul S05") == "Better Call Saul season 5"
    assert normalize_tv_search_query("Better Call Saul S01E01") == "Better Call Saul S01E01"


def test_target_seasons_from_query():
    assert target_seasons_from_query("breaking bad season 1") == [1]
    assert target_seasons_from_query("breaking bad s01") == [1]
    assert target_seasons_from_query("breaking bad") == []


def test_pack_search_single_season_no_complete_query():
    qs = [q.lower() for q in torrent_pack_search_queries("breaking bad season 1")]
    assert "breaking bad season 1" in qs
    assert not any("complete" in q for q in qs)


def test_torrent_row_matches_target_season():
    row = {
        "title": "Breaking Bad S01 1080p WEB-DL",
        "release_kind": "season",
        "season_numbers": [1],
    }
    assert torrent_row_matches_target_season(row, 1)
    full = {
        "title": "Breaking Bad S01 S05 COMPLETE 1080p",
        "release_kind": "full_show",
        "season_numbers": [1, 2, 3, 4, 5],
    }
    assert not torrent_row_matches_target_season(full, 1)
