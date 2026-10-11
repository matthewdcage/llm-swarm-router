from pathlib import Path

from download_destinations import (
    ARIA2_TORRENTS_CONTAINER_DIR,
    MOVIES_TORRENTS_HOST,
    aria2_opts_for_job,
    host_dirs_to_ensure,
    metadata_retry_scan_dir,
)


def _legacy_tv_job(**extra):
    job = {
        "id": "j1",
        "kind": "magnet",
        "destination_path": "/mnt/external/movies/torrents/Breaking Bad/Season 01",
        "url": "magnet:?xt=urn:btih:abc",
    }
    job.update(extra)
    return job


def test_begin_resume_readd_same_dir():
    job = _legacy_tv_job()
    begin = aria2_opts_for_job(job)
    resume = aria2_opts_for_job({**job, "status": "paused", "aria2_gid": "dead"})
    readd = aria2_opts_for_job({**job, "aria2_gid": None})
    assert begin["dir"] == resume["dir"] == readd["dir"] == ARIA2_TORRENTS_CONTAINER_DIR


def test_legacy_nested_job_uses_flat_dir():
    job = _legacy_tv_job(status="paused", aria2_gid="dead")
    assert aria2_opts_for_job(job)["dir"] == ARIA2_TORRENTS_CONTAINER_DIR


def test_meta_retry_scans_release_folder(tmp_path):
    job = _legacy_tv_job()
    host = tmp_path / "torrents" / "Breaking.Bad.S01.1080p"
    host.mkdir(parents=True)
    (host / "ep1.mkv").write_bytes(b"x")
    assert metadata_retry_scan_dir(job, host) == host
    assert str(metadata_retry_scan_dir(job, host)) != job["destination_path"]


def test_worker_does_not_mkdir_nested_inbox():
    dest = "/mnt/external/movies/torrents/Breaking Bad/Season 01"
    assert host_dirs_to_ensure(dest) == [Path(MOVIES_TORRENTS_HOST)]
