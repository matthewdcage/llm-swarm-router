import pytest

from download_destinations import (
    ARIA2_TORRENTS_CONTAINER_DIR,
    aria2_dir_for,
    aria2_dir_for_job,
)


def test_movies_usb_maps_torrents_subfolder():
    dest = "/mnt/external/movies/torrents/Better Call Saul/Season 01"
    assert aria2_dir_for(dest) == (
        f"{ARIA2_TORRENTS_CONTAINER_DIR}/Better Call Saul/Season 01",
        None,
    )


def test_movies_usb_root_inbox():
    # L1: aria2_dir_for inbox is /mnt/torrents, never the stale /downloads path.
    assert aria2_dir_for("/mnt/external/movies/torrents") == (
        ARIA2_TORRENTS_CONTAINER_DIR,
        None,
    )


@pytest.mark.parametrize(
    "dest",
    [
        "/mnt/external/movies/torrents",
        "/mnt/external/movies/torrents/X",
        "/mnt/external/movies/torrents/Breaking Bad/Season 01",
        "/mnt/external/movies/torrents/The Office/Season 01-09",
    ],
)
def test_aria2_dir_for_job_flat_inbox(dest):
    assert aria2_dir_for_job(dest) == (ARIA2_TORRENTS_CONTAINER_DIR, None)


def test_aria2_dir_for_job_media_hdd():
    dest = "/mnt/media/keep/file.bin"
    assert aria2_dir_for_job(dest) == aria2_dir_for(dest)
    assert aria2_dir_for_job(dest)[0].startswith("/media/")


def test_aria2_dir_for_job_tnas_staging(monkeypatch):
    monkeypatch.setattr("download_destinations.Path.is_mount", lambda self: True)
    dest = "/mnt/tnas/Website Backups/example.com"
    assert aria2_dir_for_job(dest) == (
        "/media/_nas_staging/Website Backups/example.com",
        dest,
    )


def test_aria2_dir_for_job_rejects_traversal():
    with pytest.raises(ValueError):
        aria2_dir_for_job("/mnt/external/movies/torrents/../../etc")
