from __future__ import annotations

import json

from index_writer import build_entry, load_entries
from tests.conftest import _write_job


def _patch(client, job_id, body):
    return client.patch(
        f"/api/jobs/{job_id}",
        json=body,
        headers={"x-worker-token": "test-worker"},
    )


def test_patch_done_with_index_entry_appends(hub_client):
    client, jobs_dir, _app = hub_client
    _write_job(jobs_dir, {"id": "j1", "status": "running", "kind": "magnet", "title": "X"})
    entry = build_entry("torrent", title="X", hub_job_id="j1", source_id="h")
    r = _patch(client, "j1", {"status": "done", "index_entry": entry})
    assert r.status_code == 200
    loaded = load_entries(jobs_dir / "download-index.jsonl")
    assert any(v.get("hub_job_id") == "j1" for v in loaded.values())


def test_patch_done_without_entry_appends_fallback(hub_client):
    client, jobs_dir, _app = hub_client
    _write_job(jobs_dir, {"id": "v1", "status": "running", "kind": "video", "title": "clip"})
    r = _patch(client, "v1", {"status": "done"})
    assert r.status_code == 200
    loaded = load_entries(jobs_dir / "download-index.jsonl")
    kinds = {v.get("kind") for v in loaded.values()}
    assert "video" in kinds
    assert len(loaded) == 1


def test_patch_error_records_failed(hub_client):
    client, jobs_dir, _app = hub_client
    _write_job(jobs_dir, {"id": "e1", "status": "running", "kind": "magnet"})
    r = _patch(client, "e1", {"status": "error", "error": "no peers"})
    assert r.status_code == 200
    loaded = load_entries(jobs_dir / "download-index.jsonl")
    assert next(iter(loaded.values()))["status"] == "failed"


def test_patch_running_does_not_append(hub_client):
    client, jobs_dir, _app = hub_client
    _write_job(jobs_dir, {"id": "p1", "status": "claimed", "kind": "magnet"})
    r = _patch(client, "p1", {"status": "running", "progress": {"pct": 10}})
    assert r.status_code == 200
    idx = jobs_dir / "download-index.jsonl"
    assert not idx.exists() or not idx.read_text().strip()


def test_index_entry_has_no_secrets(hub_client):
    client, jobs_dir, _app = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "s1",
            "status": "running",
            "kind": "magnet",
            "url": "http://127.0.0.1:9117/dl/x?jackett_apikey=supersecret",
        },
    )
    r = _patch(client, "s1", {"status": "done"})
    assert r.status_code == 200
    text = (jobs_dir / "download-index.jsonl").read_text()
    assert "supersecret" not in text
    row = json.loads(text.strip().splitlines()[-1])
    assert "apikey=***" in (row.get("source_url") or "")
