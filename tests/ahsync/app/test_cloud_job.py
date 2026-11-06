"""Tests for ahsync/app/cloud_job.py: a newer summary re-plans and rewrites the addon files (SPEC §2.4, BZ17)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from ahcloud.publish import publish
from ahcloud.store import LocalStore
from ahsync.app import cloud_job, refresh
from ahsync.collect import cloud_intel
from ahsync.collect.client import UrllibTransport
from tests.ahsync.app.test_refresh import HOUR, M, NOW, cloud_cfg, cloud_document, db  # noqa: F401  the db fixture
from tests.support.cloud_origin import ORIGIN, Origin
from tests.support.fake_clock import FakeClock


@pytest.fixture
def published(db, tmp_path):  # noqa: F811
    """The seeded world's document published at the origin, and the cloud config pointing at it."""
    document = cloud_document(db)
    store = LocalStore(tmp_path / "cloud")
    publish(store, document)
    cfg = replace(cloud_cfg(), cloud=replace(cloud_cfg().cloud, intel_url=ORIGIN))
    return document, store, cfg


def test_BZ17_a_new_summary_replans_remembers_and_rewrites_both_addon_files(db, published, monkeypatch):  # noqa: F811
    document, store, cfg = published
    exports = []
    monkeypatch.setattr(cloud_job, "export_market", lambda *a: exports.append("market"))
    monkeypatch.setattr(cloud_job, "export_plan", lambda *a: exports.append("plan"))
    origin = Origin(store)
    assert cloud_job.poll_cloud(db, origin, FakeClock(NOW + HOUR), cfg) == [M]
    assert db.read_intel(M) == document and exports == ["market", "plan"]
    assert cloud_job.poll_cloud(db, origin, FakeClock(NOW + 2 * HOUR), cfg) == []      # 304: nothing to do
    assert exports == ["market", "plan"]


def test_BZ17_a_failed_replan_fetches_the_document_again_next_time(db, published, monkeypatch):  # noqa: F811
    _, store, cfg = published
    monkeypatch.setattr(cloud_job, "export_market", lambda *a: None)
    monkeypatch.setattr(cloud_job, "export_plan", lambda *a: None)
    monkeypatch.setattr(cloud_job, "replan_all", lambda *a, **k: [])                  # it failed
    assert cloud_job.poll_cloud(db, Origin(store), FakeClock(NOW + HOUR), cfg) == []
    assert db.source_state(M, cloud_intel.SRC)[3] is None                               # not remembered
    monkeypatch.setattr(cloud_job, "replan_all", refresh.replan_all)
    assert cloud_job.poll_cloud(db, Origin(store), FakeClock(NOW + 2 * HOUR), cfg) == [M]


def test_a_market_that_is_not_a_cloud_market_is_never_polled(db, published):  # noqa: F811
    _, store, cfg = published
    origin = Origin(store)
    assert cloud_job.poll_cloud(db, origin, FakeClock(NOW), replace(cfg, sources={})) == [] and origin.requests == []


def test_SEC16_the_real_transport_is_replaced_by_a_bounded_one_that_refuses_redirects():
    real = cloud_job.cloud_transport(UrllibTransport())
    assert isinstance(real, UrllibTransport) and real._max_bytes == cloud_intel.MAX_BYTES
    fake = object()
    assert cloud_job.cloud_transport(fake) is fake
