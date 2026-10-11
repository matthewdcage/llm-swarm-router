"""Hub download queue ordering (queue_rank + reorder API)."""
from __future__ import annotations

import importlib
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
    monkeypatch.setenv("HUB_WORKER_TOKEN", "test-worker")
    (tmp_path / "jobs").mkdir()
    (tmp_path / "library.json").write_text('{"drives":[]}', encoding="utf-8")
    sys.path.insert(0, str(HUB_ROOT))
    if "app" in sys.modules:
        app_mod = importlib.reload(sys.modules["app"])
    else:
        app_mod = importlib.import_module("app")
    return TestClient(app_mod.app), tmp_path, app_mod


def test_enqueue_assigns_queue_rank(hub_client):
    client, jobs_dir, app_mod = hub_client
    job = app_mod._enqueue_job({"id": "a1", "status": "queued", "kind": "magnet"})
    assert isinstance(job.get("queue_rank"), int)
    on_disk = json.loads((jobs_dir / "jobs" / "a1.json").read_text())
    assert on_disk["queue_rank"] == job["queue_rank"]


def test_claim_uses_queue_rank_before_created_at(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "old",
            "status": "queued",
            "created_at": "2026-01-01T00:00:00+00:00",
            "queue_rank": 50,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "new",
            "status": "queued",
            "created_at": "2026-06-01T00:00:00+00:00",
            "queue_rank": 20,
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.status_code == 200
    assert r.json()["job"]["id"] == "new"


def test_claim_high_priority_before_queue_rank(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "low",
            "status": "queued",
            "queue_rank": 10,
            "options": {},
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "hi",
            "status": "queued",
            "queue_rank": 100,
            "options": {"high_priority": True},
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.json()["job"]["id"] == "hi"


def test_reorder_updates_ranks(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(jobs_dir, {"id": "j1", "status": "queued", "queue_rank": 10})
    _write_job(jobs_dir, {"id": "j2", "status": "queued", "queue_rank": 20})
    r = client.post(
        "/api/jobs/queue/reorder",
        json={"ordered_ids": ["j2", "j1"]},
    )
    assert r.status_code == 200
    j1 = json.loads((jobs_dir / "jobs" / "j1.json").read_text())
    j2 = json.loads((jobs_dir / "jobs" / "j2.json").read_text())
    assert j2["queue_rank"] == 10
    assert j1["queue_rank"] == 20


def test_reorder_rejects_incomplete_id_set(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(jobs_dir, {"id": "j1", "status": "queued"})
    _write_job(jobs_dir, {"id": "j2", "status": "queued"})
    r = client.post("/api/jobs/queue/reorder", json={"ordered_ids": ["j1"]})
    assert r.status_code == 400


def test_reorder_ignores_running_jobs(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(jobs_dir, {"id": "run", "status": "running"})
    _write_job(jobs_dir, {"id": "q1", "status": "queued"})
    r = client.post("/api/jobs/queue/reorder", json={"ordered_ids": ["q1", "run"]})
    assert r.status_code == 400
