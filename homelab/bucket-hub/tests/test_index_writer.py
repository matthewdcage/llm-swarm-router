from __future__ import annotations

import logging
import threading

import pytest

from index_writer import (
    CORE_FIELDS,
    SCHEMA_VERSION,
    IndexSecretError,
    append_entry,
    build_entry,
    build_torrent_entry,
    entry_from_job,
    load_entries,
)


def test_build_entry_core_fields():
    entry = build_entry("torrent", title="X", hub_job_id="j1", source_id="s1")
    for field in CORE_FIELDS:
        assert field in entry
    assert entry["schema_version"] == SCHEMA_VERSION
    assert entry["hub_tags"] == []
    assert entry["topics"] == []
    assert entry["user_note"] is None
    assert entry["ai_summary"] is None


def test_build_torrent_entry():
    job = {
        "id": "j1",
        "kind": "magnet",
        "title": "Breaking.Bad.S02.1080p",
        "torrent_meta": {"show_title_guess": "Breaking Bad", "release_kind": "season"},
        "notes": "for the weekend",
        "tags": ["tv"],
    }
    entry = build_torrent_entry(
        job,
        "torrents/Breaking.Bad.S02.1080p",
        ["e1.mkv", "e2.mkv", "sample.txt"],
        {"sonarr": "added"},
    )
    assert entry["media_type"] == "tv"
    assert entry["show_title"] == "Breaking Bad"
    assert entry["files_count"] == 3
    assert entry["video_files"] == ["e1.mkv", "e2.mkv"]
    assert "for the weekend" in (entry["context"] or "")
    assert not str(entry["rel_path"]).startswith("/")


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:9117/dl/x?jackett_apikey=abc",
        "Bearer abc",
        "ghp_" + ("a" * 36),
        "hf_" + ("b" * 30),
    ],
)
def test_secret_scan_blocks_keys(value):
    with pytest.raises(IndexSecretError):
        build_entry("torrent", title=value, hub_job_id="j1")


def test_source_url_redacted():
    job = {"id": "j9", "kind": "magnet", "url": "http://x/dl?apikey=abc123"}
    entry = entry_from_job(job)
    assert "apikey=***" in (entry["source_url"] or "")
    assert "abc123" not in (entry["source_url"] or "")


def test_append_and_last_line_wins(tmp_path):
    path = tmp_path / "download-index.jsonl"
    first = build_entry("torrent", title="A", hub_job_id="j1", source_id="s", entry_id="k:s:j1")
    second = build_entry(
        "torrent",
        title="A",
        hub_job_id="j1",
        source_id="s",
        entry_id="k:s:j1",
        user_note="keep",
    )
    append_entry(path, first)
    append_entry(path, second)
    loaded = load_entries(path)
    assert len(loaded) == 1
    assert loaded["k:s:j1"]["user_note"] == "keep"


def test_corrupt_line_skipped(tmp_path, caplog):
    path = tmp_path / "download-index.jsonl"
    a = build_entry("video", title="one", hub_job_id="a", source_id="a")
    b = build_entry("video", title="two", hub_job_id="b", source_id="b")
    path.write_text(
        '{"entry_id":"video:a:a","kind":"video"}\nNOTJSON\n'
        + '{"entry_id":"video:b:b","kind":"video"}\n',
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="bucket-hub.index_writer"):
        loaded = load_entries(path)
    assert len(loaded) == 2
    assert any("corrupt" in rec.message for rec in caplog.records)
    assert a["kind"] == "video" and b["kind"] == "video"


def test_concurrent_append(tmp_path):
    path = tmp_path / "download-index.jsonl"

    def worker(offset: int) -> None:
        for i in range(100):
            append_entry(
                path,
                build_entry(
                    "video",
                    title=f"{offset}-{i}",
                    hub_job_id=f"{offset}-{i}",
                    source_id=str(offset),
                ),
            )

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 200
    import json

    for ln in lines:
        json.loads(ln)


@pytest.mark.parametrize("kind", ["video", "github", "hf", "website_archive", ""])
def test_entry_from_job_any_kind(kind):
    job = {"id": "jx", "title": "t"}
    if kind:
        job["kind"] = kind
    entry = entry_from_job(job)
    assert entry["kind"] == (kind or "unknown")
    assert entry["schema_version"] == 1
