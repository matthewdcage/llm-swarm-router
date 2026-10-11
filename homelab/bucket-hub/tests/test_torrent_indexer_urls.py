"""Tests for torrent indexer mirror URL helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from torrent_indexer_urls import (
    attach_candidates_to_indexers,
    load_candidates_store,
    merge_indexer_candidates,
    mirror_urls_to_check,
    normalize_indexer_url,
    parse_reference_url_entries,
    urls_match,
    validate_public_http_url,
)


def test_normalize_trailing_slash() -> None:
    assert normalize_indexer_url("https://Example.com/path") == "https://example.com/path/"
    assert normalize_indexer_url("https://example.com/") == "https://example.com/"


def test_validate_blocks_private() -> None:
    with pytest.raises(ValueError, match="local"):
        validate_public_http_url("http://127.0.0.1/")
    with pytest.raises(ValueError, match="local"):
        validate_public_http_url("http://192.168.1.1/")


def test_validate_accepts_public() -> None:
    assert validate_public_http_url("https://nyaa.si/foo") == "https://nyaa.si/foo/"


def test_urls_match() -> None:
    assert urls_match("https://a.com", "https://a.com/")
    assert not urls_match("https://a.com/", "https://b.com/")


def test_load_candidates_store_dedupes_invalid() -> None:
    store = load_candidates_store(
        {
            "version": 1,
            "by_jackett_id": {
                "eztv": {
                    "candidates": [
                        {"url": "https://eztvx.to/", "label": "ok"},
                        {"url": "http://localhost/", "label": "bad"},
                    ]
                }
            },
        }
    )
    assert len(store["by_jackett_id"]["eztv"]["candidates"]) == 1


def test_parse_reference_objects() -> None:
    entries = parse_reference_url_entries(
        ["https://a.com/", {"url": "https://b.com/", "label": "B mirror"}]
    )
    assert len(entries) == 2
    assert entries[1][1] == "B mirror"


def test_mirror_urls_to_check_respects_flag() -> None:
    cands = [
        {"url": "https://hint.com/", "editable": False},
        {"url": "https://mine.com/", "editable": True},
    ]
    assert mirror_urls_to_check(cands, include_references=False) == ["https://mine.com/"]
    assert len(mirror_urls_to_check(cands, include_references=True)) == 2


def test_merge_reference_and_user() -> None:
    merged = merge_indexer_candidates(
        {"jackett_id": "1337x"},
        [{"url": "https://mirror.example.com/", "label": "mine", "source": "manual"}],
        ["https://www.1337x.to/"],
        {},
    )
    sources = {m["source"] for m in merged}
    assert "megathread" in sources
    assert "manual" in sources
    assert len(merged) == 2


def test_attach_candidates_to_indexers() -> None:
    rows = attach_candidates_to_indexers(
        [{"jackett_id": "yts", "label": "YTS"}],
        load_candidates_store(None),
        {},
        {"yts": {"reference_urls": ["https://yts.mx/"]}},
    )
    assert rows[0]["mirror_candidates"]
    assert rows[0]["mirror_candidates"][0]["source"] == "megathread"


def test_catalog_json_loads() -> None:
    catalog_path = Path(__file__).resolve().parents[1] / "torrent-indexers-catalog.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = data.get("indexers") or []
    assert len(rows) >= 28
    ids = [str(r["jackett_id"]) for r in rows if r.get("jackett_id")]
    assert len(ids) == len(set(ids))
    assert data.get("megathread_games_url", "").startswith("https://")
    assert data.get("catalog_version")
    for row in rows:
        for _url, _label in parse_reference_url_entries(row.get("reference_urls") or []):
            validate_public_http_url(_url)
