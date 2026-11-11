"""Tests for ahcloud/analyzer.py and the ingest/analyze/rebuild commands: one code path, Lambda or laptop (BZ31, BZ34)."""
from __future__ import annotations

import json
import logging

import pytest

from ahcloud import analyzer
from ahcloud.__main__ import main
from ahcloud.aws import S3Store
from ahcloud.ingest import run_ingest
from ahcloud.publish import read_latest
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import raw_key
from tests.support.cloud_world import A_MS, B_MS, C_MS, M, SNAP_A, SNAP_B, SNAP_C, FakeCatalog
from tests.support.fake_aws import ClientError, FakeS3Client
from tests.support.fake_clock import FakeClock

ENV = {"DATA_BUCKET": "data", "INTEL_BUCKET": "intel", "MARKET": M, "ALARM_PREFIX": "WowAuctionMarket-",
       "COLLECTOR_FUNCTION": "WowAuctionMarket-Collector8A1B-xYz", "INGEST_FUNCTION": "WowAuctionMarket-Ingest77C2-qRs",
       "AWS_LAMBDA_FUNCTION_NAME": "WowAuctionMarket-Analyzer3F9D-aBc"}

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
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(analyzer.aws, "S3Store", lambda bucket: S3Store(bucket, {"data": data, "intel": intel}[bucket]))
    monkeypatch.setattr(analyzer, "WORKDIR", tmp_path / "lambda-tmp")
    lines = []
    monkeypatch.setattr(analyzer.aws, "metric", lambda name, value, dims, now: lines.append(name))
    alarms = [(f"WowAuctionMarket-{name}1A2B3C4D-x", "OK", C_MS) for name in ("CollectorErrors", "AnalyzerErrors")]
    monkeypatch.setattr(analyzer.aws, "alarm_states", lambda prefix: alarms + [("Other-Alarm", "ALARM", C_MS)])
    monkeypatch.setattr(analyzer.aws, "metric_sums", lambda queries, start, end: {key: 0 for key in queries})
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


def test_CS11_every_run_records_itself_and_writes_the_status(monkeypatch, tmp_path):
    data, intel, _ = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    analyzer.handler({"mode": "hourly"}, None)                                     # no_base: still a status
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    state = json.loads(data.objects["state/analyzer.json"])
    assert (state["hourly"]["outcome"], state["daily"]["outcome"], state["daily"]["items"]) == ("no_base", "published", 2)
    assert state["daily"]["published_ms"] == state["daily"]["finished_ms"]
    key = f"status/{M}.json"
    assert intel.headers[key] == {"CacheControl": "max-age=60", "ContentType": "application/json"}
    doc = json.loads(intel.objects[key])
    assert (doc["schema"], doc["market"]) == (1, M)
    assert doc["document"][0]["value"].endswith("2 items")
    assert [r["value"].split(" · ")[1] for r in doc["functions"]["rows"][2:4]] == ["no baselines yet",
                                                                                 "baselines published"]


def test_FM81_a_failed_status_write_never_costs_the_run_its_outcome(monkeypatch, tmp_path, caplog):
    data, intel, metrics = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    data.objects[raw_key(M, C_MS)] = SNAP_C
    real_put = intel.put_object

    def denied(**kwargs):
        if kwargs["Key"].startswith("status/"):
            raise ClientError("AccessDenied", "PutObject")
        return real_put(**kwargs)

    intel.put_object = denied
    before = intel.objects[f"status/{M}.json"]
    with caplog.at_level(logging.WARNING, logger="ahcloud.analyzer"):
        assert analyzer.handler({"mode": "hourly"}, None)["state"] == "published"
    assert metrics[-2:] == ["hourly_published", "status_failed"]
    assert [r.getMessage() for r in caplog.records] == [
        f"status status/{M}.json not written: ClientError: An error occurred (AccessDenied) when calling the PutObject operation"]
    assert read_latest(S3Store("intel", intel), M)[0].observations_until_ms == C_MS
    intel.put_object = real_put
    analyzer.handler({"mode": "hourly"}, None)
    assert intel.objects[f"status/{M}.json"] != before and metrics[-1] == "hourly_up_to_date"


def test_SEC21_the_status_holds_no_bucket_function_or_alarm_name(monkeypatch, tmp_path):
    data, intel, _ = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)
    analyzer.handler({"mode": "daily"}, None)
    text = intel.objects[f"status/{M}.json"].decode("utf-8")
    for secret in ("WowAuctionMarket", "Collector8A1B", "Ingest77C2", "Analyzer3F9D", "1A2B3C4D", "Other-Alarm",
                   '"data"', '"intel"', "arn:", "Traceback"):
        assert secret not in text


def test_FM81_with_under_a_minute_left_the_status_step_is_skipped(monkeypatch, tmp_path, caplog):
    data, intel, metrics = lambda_world(monkeypatch, tmp_path, (A_MS, SNAP_A), (B_MS, SNAP_B))
    ingest_cloud(data, tmp_path)

    class NearTimeout:
        def get_remaining_time_in_millis(self):
            return 59_000

    with caplog.at_level(logging.WARNING, logger="ahcloud.analyzer"):
        assert analyzer.handler({"mode": "daily"}, NearTimeout())["state"] == "published"
    assert metrics == ["daily_published", "status_failed"]
    assert f"status/{M}.json" not in intel.objects and "state/analyzer.json" not in data.objects
    assert [r.getMessage() for r in caplog.records] == [f"status status/{M}.json not written: 59000 ms left of the run"]
