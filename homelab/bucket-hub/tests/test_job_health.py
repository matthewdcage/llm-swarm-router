from datetime import datetime, timedelta, timezone

from job_health import evaluate_job_health, update_progress_watch

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _job(**overrides):
    base = {
        "id": "abc",
        "status": "running",
        "worker": "aria2",
        "kind": "magnet",
        "title": "Training Day",
        "started_at": (NOW - timedelta(hours=26)).isoformat(),
        "aria2_gid": "deadbeef",
        "progress": {
            "phase": "metadata",
            "metadata_only": True,
            "completed": "0",
            "total": "0",
            "seeders": "0",
            "connections": "0",
        },
    }
    base.update(overrides)
    return base


def test_metadata_stuck_flags():
    review = evaluate_job_health(_job(), now=NOW)
    assert review is not None
    assert review["code"] == "metadata_stuck"
    assert "Training Day" in review["summary"]


def test_metadata_young_not_flagged():
    job = _job(
        started_at=(NOW - timedelta(minutes=20)).isoformat(),
    )
    assert evaluate_job_health(job, now=NOW) is None


def test_no_progress_when_watch_stale():
    job = _job(
        started_at=(NOW - timedelta(hours=2)).isoformat(),
        progress={
            "phase": "downloading",
            "completed": "1000",
            "total": "10000",
            "seeders": "5",
            "connections": "10",
        },
        progress_watch={
            "completed": 1000,
            "stall_since": (NOW - timedelta(minutes=120)).isoformat(),
        },
    )
    review = evaluate_job_health(job, now=NOW)
    assert review is not None
    assert review["code"] == "no_progress"


def test_progress_watch_updates_on_byte_change():
    job = {"progress_watch": {"completed": 0, "stall_since": NOW.isoformat()}}
    watch = update_progress_watch(
        job, {"completed": "500"}, now=NOW + timedelta(seconds=30)
    )
    assert watch["completed"] == 500


def test_stale_running_complete():
    job = _job(
        started_at=(NOW - timedelta(minutes=30)).isoformat(),
        progress={
            "phase": "downloading",
            "completed": "1000",
            "total": "1000",
            "seeders": "0",
            "connections": "0",
        },
    )
    review = evaluate_job_health(job, now=NOW)
    assert review is not None
    assert review["code"] == "stale_running_complete"
