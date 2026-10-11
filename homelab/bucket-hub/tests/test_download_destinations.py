from download_destinations import aria2_dir_for


def test_movies_usb_maps_torrents_subfolder():
    dest = "/mnt/external/movies/torrents/Better Call Saul/Season 01"
    from download_destinations import ARIA2_TORRENTS_CONTAINER_DIR

    assert aria2_dir_for(dest) == (
        f"{ARIA2_TORRENTS_CONTAINER_DIR}/Better Call Saul/Season 01",
        None,
    )


def test_movies_usb_root_inbox():
    assert aria2_dir_for("/mnt/external/movies") == ("/downloads", None)
    assert aria2_dir_for("/mnt/external/movies/torrents") == ("/downloads", None)
