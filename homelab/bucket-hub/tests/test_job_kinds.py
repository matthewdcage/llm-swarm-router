import logging

import pytest

from job_kinds import dispatch_route, is_aria2_job


def test_no_kind_metube_worker_is_not_aria2():
    assert is_aria2_job({"id": "j1", "worker": "metube"}) is False


def test_no_kind_no_worker_is_not_aria2(caplog):
    with caplog.at_level(logging.WARNING, logger="bucket-hub.job_kinds"):
        assert is_aria2_job({"id": "j2"}) is False
    assert any("neither worker nor kind" in rec.message for rec in caplog.records)


def test_magnet_without_worker_is_aria2():
    assert is_aria2_job({"kind": "magnet"}) is True


def test_http_without_worker_is_aria2():
    assert is_aria2_job({"kind": "http"}) is True


def test_repo_worker_never_aria2():
    assert is_aria2_job({"worker": "repo", "kind": "http"}) is False


@pytest.mark.parametrize("kind", ["github", "hf", "github_repo", "huggingface_repo"])
def test_future_repo_kinds_never_aria2(kind):
    assert is_aria2_job({"kind": kind}) is False
    assert dispatch_route({"kind": kind}) != "aria2"


def test_kindless_dispatch_never_aria2():
    assert dispatch_route({"id": "x"}) != "aria2"
    assert dispatch_route({"worker": "metube"}) == "video"
