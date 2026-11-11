"""Tests for ahcloud/analyzer.py and the ingest/analyze/rebuild commands: one code path, Lambda or laptop (BZ31, BZ34)."""
from __future__ import annotations


import pytest

from ahcloud import analyzer
from ahcloud.__main__ import main
from ahcloud.aws import S3Store
from ahcloud.ingest import run_ingest
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import raw_key
from tests.support.cloud_world import A_MS, B_MS, C_MS, M, SNAP_A, SNAP_B, SNAP_C, FakeCatalog
from tests.support.fake_aws import FakeS3Client
from tests.support.fake_clock import FakeClock

@pytest.mark.parametrize("event, mode", [({"mode": "daily"}, "daily"), ({"mode": "rebuild"}, "hourly"),
                                         ({"mode": "hourly"}, "hourly"), ({"Records": [{"s3": {}}]}, "hourly"),
                                         (None, "hourly"), ({"mode": "drop tables"}, "hourly")])
def test_the_event_picks_the_mode(event, mode):
    assert analyzer.mode_of(event) == mode


def lambda_world(monkeypatch, tmp_path, *snaps):
    """The analyzer's environment over fake buckets, no secret; returns (data client, intel client, metric lines)."""
    data, intel = FakeS3Client(), FakeS3Client()
    for at_ms, envelope in snaps:
        data.objects[raw_key(M, at_ms)] = envelope
    for name, value in {"DATA_BUCKET": "data", "INTEL_BUCKET": "intel", "MARKET": M}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(analyzer.aws, "S3Store", lambda bucket: S3Store(bucket, {"data": data, "intel": intel}[bucket]))
    monkeypatch.setattr(analyzer, "WORKDIR", tmp_path / "lambda-tmp")
    lines = []
    monkeypatch.setattr(analyzer.aws, "metric", lambda name, value, dims, now: lines.append(name))
    return data, intel, lines


def ingest_cloud(data, tmp_path):
    """The Ingest function's work over the fake data bucket."""
    run_ingest(S3Store("data", data), FakeCatalog(), M, tmp_path / "ingest", FakeClock(C_MS))


def test_BZ34_daily_then_hourly_through_the_handler(monkeypatch, tmp_path):
    data, intel, metrics = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    assert analyzer.handler({"mode": "hourly"}, None)["state"] == "no_base"     # nothing to layer over yet
    ingest_cloud(data, tmp_path)
    daily = analyzer.handler({"mode": "daily"}, None)
    assert (daily["state"], daily["built_ms"], daily["items"]) == ("published", B_MS, 2)
    data.objects[raw_key(M, C_MS)] = SNAP_C
    hourly = analyzer.handler({"Records": [{"s3": {"object": {"key": raw_key(M, C_MS)}}}]}, None)
    assert (hourly["state"], hourly["observations_until_ms"]) == ("published", C_MS)
    assert metrics == ["hourly_no_base", "daily_published", "hourly_published"]
    doc = read_latest(S3Store("intel", intel), M)[0]
    assert (doc.built_ms, doc.observations_until_ms) == (B_MS, C_MS)


def test_BZ34_a_failed_hourly_run_leaves_the_last_document_and_the_next_one_catches_up(monkeypatch, tmp_path):
    data, intel, _ = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    before = read_latest(S3Store("intel", intel), M)[1]
    data.objects[raw_key(M, C_MS)] = b"cut off"
    with pytest.raises(Exception):
        analyzer.handler({"mode": "hourly"}, None)
    assert read_latest(S3Store("intel", intel), M)[1] == before
    data.objects[raw_key(M, C_MS)] = SNAP_C
    assert analyzer.handler({"mode": "hourly"}, None)["state"] == "published"


def test_BZ31_the_laptop_writes_what_the_lambda_writes(monkeypatch, tmp_path):
    data, intel, _ = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    lambda_doc = read_latest(S3Store("intel", intel), M)[1]
    folder = tmp_path / "cloud"
    local = LocalStore(folder)
    local.put(raw_key(M, A_MS), SNAP_A)
    local.put(raw_key(M, B_MS), SNAP_B)
    home = tmp_path / "home"
    (home / ".ahsync").mkdir(parents=True)
    (home / ".ahsync" / "blizzard").write_text("client-id-1234\nclient-secret-5678\n", encoding="utf-8")
    monkeypatch.setattr("ahcloud.__main__.BlizzardClient", lambda *args: FakeCatalog())
    assert main(["ingest", "--store", str(folder)], env={}, home=home) == 0
    empty = tmp_path / "empty-home"
    assert main(["analyze", "--daily", "--store", str(folder)], env={}, home=empty) == 0   # no credentials needed
    assert read_latest(local, M)[1].sha256 == lambda_doc.sha256                  # byte for byte
    assert main(["analyze", "--hourly", "--store", str(folder)], env={}, home=empty) == 0


def test_BZ31_out_reproduces_a_cloud_run_without_writing_to_the_cloud(monkeypatch, tmp_path):
    data, intel, _ = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    cloud_doc = read_latest(S3Store("intel", intel), M)[1]
    before = (dict(data.objects), dict(intel.objects))
    monkeypatch.setattr("ahcloud.__main__.aws.S3Store", lambda bucket: S3Store(bucket, {"data": data, "intel": intel}[bucket]))
    monkeypatch.setattr("ahcloud.__main__.BlizzardClient", lambda *args: FakeCatalog())
    out = tmp_path / "repro"
    env = {"BLIZZARD_CLIENT_ID": "a", "BLIZZARD_CLIENT_SECRET": "b"}
    assert main(["rebuild", "--store", "s3://data", "--intel", "s3://intel", "--out", str(out)], env=env, home=tmp_path) == 0
    assert (data.objects, intel.objects) == before                              # the buckets were only read
    assert read_latest(LocalStore(out), M)[1].sha256 == cloud_doc.sha256         # the rebuild matches the cloud's
