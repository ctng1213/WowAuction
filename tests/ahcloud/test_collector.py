"""Tests for ahcloud/collector.py: one run stores a new snapshot's raw envelope (SPEC §2.4, M30)."""
from __future__ import annotations

import gzip
import json
import logging

import pytest

from ahcloud import aws, collector
from ahcloud.__main__ import main
from ahcloud.aws import S3Store
from ahcloud.collector import STATE_KEY, Settings, run
from ahcloud.store import LocalStore
from ahsync.collect.blizzard import BlizzardClient, BlizzardError
from tests.support.blizzard_http import B, B_MS, C, BlizzardHttp, recorded
from tests.support.fake_aws import ClientError, FakeS3Client, FakeSecrets
from tests.support.fake_clock import FakeClock

M = "forever.normal.horde.us"
SETTINGS = Settings(M, 2, 113)
KEY_B = "raw/forever.normal.horde.us/2026/11/06/140240.json.gz"
SECRET_JSON = json.dumps({"client_id": "client-id-1234", "client_secret": "client-secret-5678"})


def collect(store, http, clock=None):
    """One run with a fresh client; returns (outcome, metrics)."""
    clock = clock or FakeClock(B_MS + 60_000)
    metrics = []
    client = BlizzardClient(http, clock, "client-id-1234", "client-secret-5678")
    return run(store, client, clock, SETTINGS, lambda name, value: metrics.append((name, value))), metrics


def test_BZ7_a_new_snapshot_writes_its_envelope_then_the_state_file_and_the_metric(tmp_path):
    store = LocalStore(tmp_path)
    outcome, metrics = collect(store, BlizzardHttp())
    assert (outcome.state, outcome.key, outcome.last_modified) == ("written", KEY_B, B)
    envelope = json.loads(gzip.decompress(store.get(KEY_B).data))
    assert (envelope["house"], envelope["ruleset"], envelope["last_modified"], envelope["fetched_ms"]) == (113, 2, B, B_MS + 60_000)
    assert envelope["auctions"] == json.loads(recorded("house_113_auctions"))
    assert json.loads(store.get(STATE_KEY).data) == {"last_modified": B}
    assert metrics == [("snapshot_written", 1)]
    assert outcome.auctions + outcome.bid_only == len(envelope["auctions"]["auctions"]) + len(envelope["commodities"]["auctions"])


def test_BZ8_unchanged_writes_nothing_and_a_crash_before_the_state_file_rewrites_the_same_bytes(tmp_path):
    store, http = LocalStore(tmp_path), BlizzardHttp()
    collect(store, http)
    first = store.get(KEY_B).data
    outcome, metrics = collect(store, http)                                   # 14:20: both 304
    assert outcome.state == "unchanged" and metrics == [("unchanged", 1)]
    assert http.requests[-1].headers["If-Modified-Since"] == B
    (tmp_path / "state" / "collector.json").unlink()                          # as if the run died before it
    outcome, _ = collect(store, http)
    assert outcome.key == KEY_B and store.get(KEY_B).data == first           # same key, same bytes
    assert store.list("raw/") == [KEY_B]


def test_BZ8_a_new_refresh_adds_the_next_hours_object():
    store, http = S3Store("bucket", FakeS3Client()), BlizzardHttp()
    collect(store, http)
    http.stamp = C
    outcome, _ = collect(store, http)
    assert outcome.key == "raw/forever.normal.horde.us/2026/11/06/150205.json.gz"
    assert store.list("raw/") == [KEY_B, outcome.key]


def test_BZ9_halves_from_different_refreshes_write_nothing(tmp_path):
    store, http = LocalStore(tmp_path), BlizzardHttp()
    collect(store, http)
    auctions_new, commodities_old = BlizzardHttp(C), BlizzardHttp(B)          # only the auctions have refreshed

    def split(request):
        return (commodities_old if "/commodities" in request.url else auctions_new)(request)

    outcome, metrics = collect(store, split)
    assert outcome.state == "mismatch" and metrics == [("mismatch", 1)]
    assert store.list("raw/") == [KEY_B] and json.loads(store.get(STATE_KEY).data) == {"last_modified": B}


def test_FM68_a_failed_run_writes_nothing_and_raises_after_one_attempt(tmp_path):
    store, http = LocalStore(tmp_path), BlizzardHttp()
    http.status["commodities"] = 503
    with pytest.raises(BlizzardError, match="answered 503"):
        collect(store, http)
    assert store.list("") == []
    assert sum(1 for r in http.requests if r.method == "GET") == 2            # one try at each endpoint, no retry
    http.status["auctions"] = 429
    with pytest.raises(BlizzardError, match="answered 429"):
        collect(store, http)


def lambda_env(monkeypatch, s3, secrets, http):
    """The Lambda's environment and AWS clients, faked; returns nothing."""
    for name, value in {"BUCKET": "wowauction-market", "MARKET": M, "RULESET": "2", "HOUSE": "113",
                        "SECRET_NAME": "wowauction/blizzard-api"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(collector, "_client", None)
    monkeypatch.setattr(collector.aws, "S3Store", lambda bucket: S3Store(bucket, s3))
    real_get_secret = aws.get_secret                                          # patched below: keep the original
    monkeypatch.setattr(collector.aws, "get_secret", lambda name: real_get_secret(name, secrets))
    monkeypatch.setattr(collector, "UrllibTransport", lambda: http)


def test_BZ6_a_warm_lambda_reuses_its_client_and_token(monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    s3, secrets, http = FakeS3Client(), FakeSecrets({"wowauction/blizzard-api": SECRET_JSON}), BlizzardHttp()
    lambda_env(monkeypatch, s3, secrets, http)
    assert collector.handler({}, None)["state"] == "written"
    assert collector.handler({}, None)["state"] == "unchanged"
    assert secrets.calls == ["wowauction/blizzard-api"] and http.tokens == 1   # one secret read, one token
    out = capsys.readouterr().out + caplog.text
    assert "snapshot_written" in out and all(s not in out for s in ("client-id-1234", "client-secret-5678", "token-0001"))
    assert all(k not in {"BLIZZARD_CLIENT_ID", "BLIZZARD_CLIENT_SECRET"} for k in __import__("os").environ if k.startswith("BLIZZARD"))


def test_FM67_an_unreadable_secret_fails_the_run_naming_the_secret_only(monkeypatch, caplog):
    s3, http = FakeS3Client(), BlizzardHttp()
    lambda_env(monkeypatch, s3, FakeSecrets(denied=True), http)
    with pytest.raises(ClientError, match="AccessDeniedException") as error:
        collector.handler({}, None)
    assert "client-secret" not in str(error.value) and s3.objects == {} and http.requests == []


def test_BZ31_the_command_line_writes_what_the_lambda_writes(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".ahsync").mkdir(parents=True)
    (home / ".ahsync" / "blizzard").write_text("client-id-1234\nclient-secret-5678\n", encoding="utf-8")
    monkeypatch.setattr("ahcloud.__main__.SystemClock", lambda: FakeClock(B_MS + 60_000))
    folder = tmp_path / "cloud"
    assert main(["collect", "--store", str(folder), "--house", "113"], env={}, home=home, transport=BlizzardHttp()) == 0
    assert "written: " + KEY_B in capsys.readouterr().out
    s3 = FakeS3Client()
    monkeypatch.setattr(collector, "SystemClock", lambda: FakeClock(B_MS + 60_000))
    lambda_env(monkeypatch, s3, FakeSecrets({"wowauction/blizzard-api": SECRET_JSON}), BlizzardHttp())
    collector.handler({}, None)
    local = LocalStore(folder)
    assert sorted(s3.objects) == local.list("") == [KEY_B, STATE_KEY]
    assert all(s3.objects[k] == local.get(k).data for k in s3.objects)       # byte for byte


def test_BZ31_a_credential_on_the_command_line_is_refused_before_any_request(tmp_path, capsys):
    http = BlizzardHttp()
    assert main(["collect", "--house", "113", "--client-secret=abc"], env={}, home=tmp_path, transport=http) == 2
    err = capsys.readouterr().err
    assert "set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET, or ~/.ahsync/blizzard" in err and "abc" not in err
    assert http.requests == []
    assert main(["collect", "--house", "113"], env={}, home=tmp_path, transport=http) == 2   # no credentials anywhere
    assert http.requests == []
