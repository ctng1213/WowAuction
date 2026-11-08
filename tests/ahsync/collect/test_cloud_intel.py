"""Tests for ahsync/collect/cloud_intel.py: reading the published summary (SPEC §2.4, BZ17-BZ19, FM71, SEC16)."""
from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from ahcloud.publish import publish
from ahcloud.store import LocalStore
from ahsync.collect import cloud_intel
from ahsync.collect.client import HttpResponse, TransportError
from ahsync.db.sqlite import Database
from ahsync.intel.contract import INTEL_SCHEMA_VERSION, MarketIntelligence, to_json
from tests.support.cloud_origin import ORIGIN, Origin
from tests.support.fake_clock import FakeClock

M = "forever.normal.horde.us"
DOC = MarketIntelligence(M, 1_000, 2_000, 4, {}, settings={"default_sell_prob": 0.25}, ages_at_ms=2_000)


@pytest.fixture
def world(tmp_path):
    """A published document, its origin, and a desktop database."""
    store = LocalStore(tmp_path / "cloud")
    publish(store, DOC)
    db = Database.open(tmp_path / "a.db", FakeClock().now_ms(), None)
    yield store, Origin(store), db
    db.close()


def test_BZ17_a_new_document_is_fetched_checked_and_returned_then_304_is_nothing(world):
    store, origin, db = world
    result = cloud_intel.poll(db, origin, ORIGIN, M, 5_000)
    assert result.state == "new" and result.intel == DOC and result.sha256
    assert origin.requests[0].url == f"{ORIGIN}/intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json"
    assert all("Authorization" not in r.headers for r in origin.requests)       # no AHF key leaves for the cloud
    cloud_intel.remember(db, M, result.etag, result.sha256, 5_000)
    assert cloud_intel.poll(db, origin, ORIGIN, M, 6_000).state == "unchanged"   # If-None-Match: 304
    assert origin.requests[-1].headers["If-None-Match"] == result.etag


def test_BZ17_a_document_not_yet_remembered_is_fetched_again(world):
    _, origin, db = world
    assert cloud_intel.poll(db, origin, ORIGIN, M, 5_000).state == "new"
    assert cloud_intel.poll(db, origin, ORIGIN, M, 6_000).state == "new"      # the re-plan failed: try again


def test_BZ18_a_newer_schema_is_refused_with_its_status_line(world):
    store, origin, db = world
    body = json.loads(to_json(DOC))
    body["schema_version"] = INTEL_SCHEMA_VERSION + 1
    data = gzip.compress(json.dumps(body).encode(), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    store.put(f"intel/{M}/{digest}.json.gz", data)
    store.put(f"intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json", json.dumps({"key": f"intel/{M}/{digest}.json.gz",
                                                                            "sha256": digest}).encode())
    result = cloud_intel.poll(db, origin, ORIGIN, M, 5_000)
    assert result.state == "failed" and "newer AHSync" in result.reason
    assert [r.topic for r in db.statuses()] == [f"cloud_schema:{M}"]


def test_FM71_a_document_whose_hash_differs_from_the_pointer_is_refused(world):
    store, origin, db = world
    pointer = json.loads(store.get(f"intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json").data)
    store.put(pointer["key"], gzip.compress(b'{"torn": true}', mtime=0))
    result = cloud_intel.poll(db, origin, ORIGIN, M, 5_000)
    assert result.state == "failed" and "hash differs" in result.reason
    assert any(r.topic == f"cloud_fetch:{M}" for r in db.statuses())


@pytest.mark.parametrize("key, reason", [
    ("state/history.db", "outside intel/"),
    (f"intel/{M}/../x.json.gz", "outside intel/"),
    (f"intel/forever.pvp.horde.us/{'a' * 64}.json.gz", "outside intel/"),
    (f"https://evil.example/intel/{M}/{'a' * 64}.json.gz", "outside intel/")])
def test_SEC16_the_pointer_may_only_name_this_markets_document(world, key, reason):
    store, origin, db = world
    store.put(f"intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json", json.dumps({"key": key, "sha256": "a" * 64}).encode())
    result = cloud_intel.poll(db, origin, ORIGIN, M, 5_000)
    assert result.state == "failed" and reason in result.reason
    assert len(origin.requests) == 1                                          # the bad key is never fetched



def test_SEC16_a_summary_of_a_real_markets_size_is_read():
    body = b"{" + b" " * (100 * 1024 * 1024) + b"}"           # 100 MB: the real market's was 88 MB on 2026-11-08
    assert len(cloud_intel.inflate(gzip.compress(body, compresslevel=1, mtime=0))) == len(body)

def test_SEC16_an_inflating_document_another_markets_and_a_redirect_are_refused(world, monkeypatch):
    store, origin, db = world
    monkeypatch.setattr(cloud_intel, "MAX_BYTES", 1000)
    bomb = gzip.compress(b"{" + b" " * 5000 + b"}", mtime=0)
    digest = hashlib.sha256(bomb).hexdigest()
    store.put(f"intel/{M}/{digest}.json.gz", bomb)
    store.put(f"intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json", json.dumps({"key": f"intel/{M}/{digest}.json.gz", "sha256": digest}).encode())
    assert "inflates past 1000" in cloud_intel.poll(db, origin, ORIGIN, M, 5_000).reason
    monkeypatch.setattr(cloud_intel, "MAX_BYTES", 256 * 1024 * 1024)
    other = MarketIntelligence("forever.pvp.horde.us", 1, 1, 4, {})
    publish(store, other)
    pointer = json.loads(store.get(f"intel/forever.pvp.horde.us/latest-{INTEL_SCHEMA_VERSION}.json").data)
    moved = pointer["key"].replace("forever.pvp.horde.us", M)
    store.put(moved, store.get(pointer["key"]).data)
    store.put(f"intel/{M}/latest-{INTEL_SCHEMA_VERSION}.json", json.dumps({**pointer, "key": moved}).encode())
    assert "not forever.normal.horde.us's" in cloud_intel.poll(db, origin, ORIGIN, M, 5_000).reason

    def redirect(request):
        return HttpResponse(302, {"location": "https://elsewhere/"}, b"")

    assert "answered 302" in cloud_intel.poll(db, redirect, ORIGIN, M, 5_000).reason


def test_BZ19_offline_leaves_the_last_good_document_and_says_so(world):
    _, origin, db = world
    origin.fail = TransportError("URLError (gaierror)")
    result = cloud_intel.poll(db, origin, ORIGIN, M, 5_000)
    assert result.state == "failed" and "URLError" in result.reason and result.intel is None
    origin.fail = None
    assert cloud_intel.poll(db, origin, ORIGIN, M, 6_000).state == "new"
    assert not any(r.topic.startswith("cloud_") for r in db.statuses())          # cleared by the good read
