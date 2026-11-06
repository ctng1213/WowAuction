"""Tests for ahcloud/aws.py: the boto3 edge, over fake clients (the Store contract itself is in test_store.py)."""
from __future__ import annotations

import json

import pytest

from ahcloud.aws import NAMESPACE, S3Store, get_secret, metric
from tests.support.fake_aws import ClientError, FakeS3Client, FakeSecrets


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
