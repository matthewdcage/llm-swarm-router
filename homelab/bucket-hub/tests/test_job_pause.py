"""Hub job pause/resume API tests."""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

HUB_ROOT = Path(__file__).resolve().parents[1]


def _write_job(jobs_dir: Path, job: dict) -> None:
    sub = jobs_dir / "jobs"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{job['id']}.json").write_text(json.dumps(job), encoding="utf-8")


@pytest.fixture()
def hub_client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DIR", str(tmp_path))
    (tmp_path / "jobs").mkdir()
    (tmp_path / "library.json").write_text('{"drives":[]}', encoding="utf-8")
    sys.path.insert(0, str(HUB_ROOT))
    if "app" in sys.modules:
        app_mod = importlib.reload(sys.modules["app"])
    else:
        app_mod = importlib.import_module("app")
    return TestClient(app_mod.app), tmp_path


def test_pause_rejects_non_aria2(hub_client):
    client, jobs_dir = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "j1",
            "status": "running",
            "worker": "metube",
            "aria2_gid": "abc",
        },
    )
    r = client.post("/api/jobs/j1/pause")
    assert r.status_code == 400


def test_pause_running_aria2_keeps_gid(hub_client):
    client, jobs_dir = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "j2",
            "status": "running",
            "worker": "aria2",
            "aria2_gid": "e4a8f652404c68df",
            "progress": {"completed": 100, "total": 1000},
        },
    )
    r = client.post("/api/jobs/j2/pause")
    assert r.status_code == 200
    body = r.json()
    assert body["job"]["status"] == "paused"
    assert body["job"]["aria2_gid"] == "e4a8f652404c68df"
    assert body["job"]["progress"]["completed"] == 100


def test_resume_paused_queues_high_priority(hub_client):
    client, jobs_dir = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "j3",
            "status": "paused",
            "worker": "aria2",
            "aria2_gid": "gid1",
            "options": {},
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "j4",
            "status": "running",
            "worker": "aria2",
        },
    )
    r = client.post("/api/jobs/j3/resume")
    assert r.status_code == 200
    body = r.json()
    assert body["job"]["status"] == "queued"
    assert body["job"]["options"]["high_priority"] is True
    assert body["other_active"] == 1


def test_cancel_aria2_with_purge_sets_purging(hub_client):
    client, jobs_dir = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "j5",
            "status": "paused",
            "worker": "aria2",
            "aria2_gid": "abc123",
            "kind": "magnet",
        },
    )
    r = client.post("/api/jobs/j5/cancel", json={"purge": True})
    assert r.status_code == 200
    body = r.json()
    assert body["purge"] is True
    assert body["job"]["status"] == "purging"
    assert body["job"]["aria2_gid"] == "abc123"


def test_should_exit_wait_for_job():
    spec = importlib.util.spec_from_file_location(
        "hub_worker_pause_test",
        HUB_ROOT / "hub-worker.py",
    )
    assert spec and spec.loader
    worker = importlib.util.module_from_spec(spec)
    sys.modules["hub_worker_pause_test"] = worker
    spec.loader.exec_module(worker)
    assert worker.should_exit_wait_for_job("purging", None) == "cancelled"
    assert worker.should_exit_wait_for_job("paused", None) == "paused"
    assert worker.should_exit_wait_for_job("error", "Cancelled by user") == "cancelled"
    assert worker.should_exit_wait_for_job("running", None) is None
