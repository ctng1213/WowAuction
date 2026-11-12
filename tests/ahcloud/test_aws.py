"""Tests for ahcloud/aws.py: the boto3 edge, over fake clients (the Store contract itself is in test_store.py)."""
from __future__ import annotations

import json

import pytest

from datetime import datetime, timezone

from ahcloud.aws import NAMESPACE, S3Store, alarm_states, get_secret, metric, metric_sums
from tests.support.fake_aws import ClientError, FakeCloudWatch, FakeS3Client, FakeSecrets


def test_a_conditional_put_sends_if_match_and_an_unconditional_one_does_not():
    client = FakeS3Client()
    store = S3Store("wowauction-market", client)
    etag = store.put("state/history.db", b"v1")
    store.put("state/history.db", b"v2", if_match=etag)
    assert [c[3] for c in client.calls if c[0] == "put"] == [None, etag]
    assert all(c[1] == "wowauction-market" for c in client.calls)


def test_an_s3_error_that_is_not_a_precondition_is_raised_as_it_is():
    client = FakeS3Client()

    def denied(**_kwargs):
        raise ClientError("AccessDenied", "PutObject")

    client.put_object = denied
    with pytest.raises(ClientError, match="AccessDenied"):
        S3Store("b", client).put("raw/x.json.gz", b"x")


def test_get_secret_returns_the_secret_string():
    secrets = FakeSecrets({"wowauction/blizzard-api": '{"client_id": "a", "client_secret": "b"}'})
    assert json.loads(get_secret("wowauction/blizzard-api", secrets)) == {"client_id": "a", "client_secret": "b"}


def test_a_metric_is_one_embedded_metric_format_line():
    lines = []
    metric("snapshot_written", 1, {"market": "forever.normal.horde.us"}, 1_793_973_760_000, lines.append)
    [line] = lines
    data = json.loads(line)
    assert data["snapshot_written"] == 1 and data["market"] == "forever.normal.horde.us"
    directive = data["_aws"]["CloudWatchMetrics"][0]
    assert directive == {"Namespace": NAMESPACE, "Dimensions": [["market"]], "Metrics": [{"Name": "snapshot_written", "Unit": "Count"}]}
    assert data["_aws"]["Timestamp"] == 1_793_973_760_000


def test_CS11_the_status_put_carries_its_cache_control_and_content_type():
    client = FakeS3Client()
    S3Store("intel", client).put("status/m.json", b"{}", cache_control="max-age=60", content_type="application/json")
    S3Store("intel", client).put("intel/m/latest-16.json", b"{}")
    assert client.headers == {"status/m.json": {"CacheControl": "max-age=60", "ContentType": "application/json"},
                              "intel/m/latest-16.json": {}}


def test_SEC21_alarm_states_read_only_the_stacks_alarms_by_name_every_page():
    when = datetime(2026, 11, 10, 11, 15, tzinfo=timezone.utc)
    alarms = [{"AlarmName": f"WowAuctionMarket-{name}", "StateValue": state, "StateUpdatedTimestamp": when}
              for name, state in (("NoNewSnapshot", "OK"), ("CollectorErrors", "ALARM"), ("IngestErrors", "OK"))]
    later = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)                  # an update that kept the state
    alarms[1] = {**alarms[1], "StateUpdatedTimestamp": later, "StateTransitionedTimestamp": when}
    client = FakeCloudWatch(alarms + [{"AlarmName": "Other-Alarm", "StateValue": "ALARM", "StateUpdatedTimestamp": when}])
    names = ["WowAuctionMarket-CollectorErrors", "WowAuctionMarket-IngestErrors", "WowAuctionMarket-NoNewSnapshot",
             "WowAuctionMarket-HistoryBehind"]                                  # one not created yet: simply absent
    found = alarm_states(names, client)
    assert found == [("WowAuctionMarket-CollectorErrors", "ALARM", 1794309300000),
                     ("WowAuctionMarket-IngestErrors", "OK", 1794309300000),
                     ("WowAuctionMarket-NoNewSnapshot", "OK", 1794309300000)]
    assert client.calls == [("describe_alarms", tuple(names), ("MetricAlarm",))]


def test_CS5_metric_sums_add_every_page_and_read_0_without_data():
    client = FakeCloudWatch(values={"collector": [[3.0, 1.0], [2.0]], "mismatch": [[1.0], []]})
    queries = {"collector": ("AWS/Lambda", "Errors", {"FunctionName": "c"}),
               "mismatch": ("WowAuction", "mismatch", {"market": "m"}), "refused": ("WowAuction", "ingest_refused", {})}
    assert metric_sums(queries, 1794234600000, 1794321000000, client) == {"collector": 6, "mismatch": 1, "refused": 0}
    assert [c[4] for c in client.calls] == [None, "1"]
    assert client.calls[0][2] == datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc)
