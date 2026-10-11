"""Hub job claim slot limits (concurrent aria2 / video / archive)."""
from __future__ import annotations

from tests.conftest import _write_job


def test_status_exposes_max_concurrent_aria2(hub_client):
    client, _jobs_dir, _app_mod = hub_client
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json().get("max_concurrent_aria2") == 2


def test_claim_skips_third_aria2_when_two_running(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "a1",
            "status": "running",
            "kind": "magnet",
            "queue_rank": 10,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "a2",
            "status": "running",
            "kind": "http",
            "queue_rank": 20,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "a3",
            "status": "queued",
            "kind": "magnet",
            "queue_rank": 5,
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.status_code == 200
    assert r.json().get("job") is None


def test_claim_video_while_aria2_active(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "t1",
            "status": "running",
            "kind": "magnet",
            "queue_rank": 10,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "v1",
            "status": "queued",
            "kind": "video",
            "queue_rank": 20,
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.status_code == 200
    assert r.json()["job"]["id"] == "v1"


def test_claim_archive_blocked_while_aria2_running(hub_client):
    client, jobs_dir, _app_mod = hub_client
    _write_job(
        jobs_dir,
        {
            "id": "t1",
            "status": "running",
            "kind": "magnet",
            "queue_rank": 10,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "w1",
            "status": "queued",
            "kind": "website_archive",
            "queue_rank": 5,
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.status_code == 200
    assert r.json().get("job") is None


def test_claim_bucket_for_kindless_job(hub_client):
    client, jobs_dir, app_mod = hub_client
    app_mod.HUB_MAX_CONCURRENT_ARIA2 = 1
    _write_job(
        jobs_dir,
        {
            "id": "v1",
            "status": "running",
            "worker": "metube",
            "queue_rank": 10,
        },
    )
    _write_job(
        jobs_dir,
        {
            "id": "m1",
            "status": "queued",
            "kind": "magnet",
            "queue_rank": 20,
        },
    )
    r = client.post("/api/jobs/claim", headers={"x-worker-token": "test-worker"})
    assert r.status_code == 200
    assert r.json()["job"]["id"] == "m1"


def test_job_claim_allowed_unit(hub_client):
    _client, _jobs_dir, app_mod = hub_client
    counts = {"aria2": 2, "video": 0, "archive": 0, "exclusive": 0, "other": 0}
    assert not app_mod._job_claim_allowed({"kind": "magnet"}, counts)
    counts["aria2"] = 1
    assert app_mod._job_claim_allowed({"kind": "magnet"}, counts)
    assert app_mod._job_claim_allowed({"kind": "video"}, counts)
    counts["aria2"] = 1
    assert not app_mod._job_claim_allowed({"kind": "website_archive"}, counts)
