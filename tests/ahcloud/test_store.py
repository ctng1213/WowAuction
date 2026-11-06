"""Tests for ahcloud/store.py: the Store contract, run against LocalStore and S3Store alike."""
from __future__ import annotations

import pytest

from ahcloud.aws import S3Store
from ahcloud.store import LocalStore, PreconditionFailed, check_key
from tests.support.fake_aws import FakeS3Client


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    """Each store kind, empty."""
    if request.param == "local":
        return LocalStore(tmp_path / "cloud")
    return S3Store("bucket", FakeS3Client(page_size=2))


def test_a_missing_object_is_none_and_a_written_one_reads_back(store):
    assert store.get("raw/m/2026/11/06/140240.json.gz") is None
    etag = store.put("raw/m/2026/11/06/140240.json.gz", b"abc")
    found = store.get("raw/m/2026/11/06/140240.json.gz")
    assert found.data == b"abc" and found.etag == etag


def test_a_conditional_write_succeeds_only_on_the_etag_it_read(store):
    first = store.put("state/history.db", b"v1")
    second = store.put("state/history.db", b"v2", if_match=first)
    with pytest.raises(PreconditionFailed):
        store.put("state/history.db", b"v3", if_match=first)          # another run wrote v2 meanwhile
    with pytest.raises(PreconditionFailed):
        store.put("state/missing.db", b"x", if_match=second)          # a conditional write needs the object
    assert store.get("state/history.db").data == b"v2"


def test_list_is_sorted_by_prefix_after_a_key_and_spans_pages(store):
    for key in ("raw/m/2026/11/06/150205.json.gz", "raw/m/2026/11/06/140240.json.gz", "raw/m/2026/11/07/010000.json.gz",
                "raw/other/2026/11/06/140240.json.gz", "state/collector.json"):
        store.put(key, b"x")
    assert store.list("raw/m/") == ["raw/m/2026/11/06/140240.json.gz", "raw/m/2026/11/06/150205.json.gz",
                                    "raw/m/2026/11/07/010000.json.gz"]
    assert store.list("raw/m/", start_after="raw/m/2026/11/06/140240.json.gz") == [
        "raw/m/2026/11/06/150205.json.gz", "raw/m/2026/11/07/010000.json.gz"]
    assert store.list("intel/") == []


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../x", "raw/../../x", "raw//x", "raw/./x", "raw/a b", "raw/\\x"])
def test_a_key_that_could_leave_the_store_is_refused(key, tmp_path):
    with pytest.raises(ValueError):
        check_key(key)
    with pytest.raises(ValueError):
        LocalStore(tmp_path).put(key, b"x")
    assert list(tmp_path.iterdir()) == []


def test_a_local_write_is_atomic_and_leaves_no_temporary_file(tmp_path, monkeypatch):
    store = LocalStore(tmp_path)
    store.put("state/collector.json", b"old")

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("ahcloud.store.os.replace", fail)
    with pytest.raises(OSError):
        store.put("state/collector.json", b"new")
    assert store.get("state/collector.json").data == b"old"
    assert [p.name for p in (tmp_path / "state").iterdir()] == ["collector.json"]
