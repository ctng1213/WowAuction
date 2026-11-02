"""Tests for app/update_check.py: the daily check against GitHub Releases (SPEC §3.7.8; DR13–DR15, DR25, FM52)."""
from __future__ import annotations

import calendar
import logging

import pytest

from ahsync.app import update_check
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.updates import update_text
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import GITHUB_REPO, FakeTransport

FRI_0600 = calendar.timegm((2026, 11, 6, 6, 0, 0)) * 1000
HOUR = 3_600_000
PAGE = "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0"


@pytest.fixture
def db(tmp_path):
    database = Database.open(tmp_path / "ahsync.db", FRI_0600, None)
    yield database
    database.close()


def line(db: Database, version: str = "0.3.1", config: Config = Config(), now: int = FRI_0600) -> dict:
    """The Update line from what was stored."""
    return update_text(update_check.read_state(db, version, config), now, 0)


def test_DR13_a_newer_release_is_stored_and_announced(db):
    transport, clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
    config = Config(auto_update=False)
    assert update_check.check(db, transport, clock, "0.3.1", config)
    stored = update_check.read_meta(db)
    assert {k: stored[k] for k in ("tag", "url", "checked_ms", "etag")} == {
        "tag": "v0.4.0", "url": PAGE, "checked_ms": str(FRI_0600), "etag": '"e1"'}
    assert line(db, config=config) == {"text": "AHSync 0.4.0 is available · you run 0.3.1", "url": PAGE,
                                       "pill": "update available", "restart": False, "class": "amber"}


def test_DR15_once_a_day_with_the_etag_and_never_the_ahf_key(db):
    transport, clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
    assert update_check.check(db, transport, clock, "0.3.1", Config(api_key="ahf-secret"))
    clock.set(FRI_0600 + 12 * HOUR)                                       # Fri 18:00
    assert not update_check.check(db, transport, clock, "0.3.1", Config()) and len(transport.requests) == 1
    clock.set(FRI_0600 + 24 * HOUR)                                       # Sat 06:00: 304
    assert update_check.check(db, transport, clock, "0.3.1", Config())
    second = transport.requests[-1]
    assert second.headers["If-None-Match"] == '"e1"' and second.headers["Accept"] == "application/vnd.github+json"
    assert all("Authorization" not in r.headers for r in transport.requests)
    assert update_check.read_meta(db)["tag"] == "v0.4.0" and update_check.read_meta(db)["checked_ms"] == \
        str(FRI_0600 + 24 * HOUR)
    newer = (FakeTransport.serve_github().routes[f"{GITHUB_REPO}/releases/latest"]
             .replace(b"0.4.0", b"0.4.1"))
    transport.serve_etag(f"{GITHUB_REPO}/releases/latest", newer, '"e2"')
    clock.set(FRI_0600 + 48 * HOUR)                                       # Sun 06:00: 200 with v0.4.1
    update_check.check(db, transport, clock, "0.3.1", Config())
    assert (update_check.read_meta(db)["tag"], update_check.read_meta(db)["etag"]) == ("v0.4.1", '"e2"')


@pytest.mark.parametrize("fault, logged, text", [
    ("offline", "update check: offline", "not checked: offline since Fri 06:00"),
    (404, "update check: 404", "not checked: no release yet"),
    (403, "update check: 403", "not checked: GitHub unavailable since Fri 06:00"),
    ("no tag", "update check: no tag_name", "not checked: GitHub unavailable since Fri 06:00"),
])
def test_FM52_a_check_without_a_release_logs_once_and_waits_a_day(db, caplog, fault, logged, text):
    transport, clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
    path = f"{GITHUB_REPO}/releases/latest"
    if fault == "offline":
        transport.network_error(path)
    elif fault == "no tag":
        transport.serve_bytes(path, b'{"html_url": "https://github.com/x"}')
    else:
        transport.fail(path, fault)
    with caplog.at_level(logging.INFO, logger="ahsync.update"):
        assert update_check.check(db, transport, clock, "0.3.1", Config())
    assert [r.getMessage() for r in caplog.records if r.name == "ahsync.update"] == [logged]
    assert line(db) == {"text": text, "url": None, "pill": None, "restart": False, "class": "muted"}
    clock.set(FRI_0600 + 23 * HOUR)
    assert not update_check.check(db, transport, clock, "0.3.1", Config())    # Sat 06:00 is the next one
    clock.set(FRI_0600 + 24 * HOUR)
    assert update_check.check(db, transport, clock, "0.3.1", Config())
    if fault == "offline":
        assert line(db, now=FRI_0600 + 24 * HOUR)["text"] == "not checked: offline since Fri 06:00"   # the first failure


def test_FM52_an_unreadable_body_is_github_unavailable(db):
    transport = FakeTransport.serve_github()
    transport.serve_bytes(f"{GITHUB_REPO}/releases/latest", b"<html>")
    update_check.check(db, transport, FakeClock(FRI_0600), "0.3.1", Config())
    assert line(db)["text"] == "not checked: GitHub unavailable since Fri 06:00"


def test_DR24_a_dev_build_or_update_check_off_never_asks(db):
    transport = FakeTransport.serve_github()
    assert not update_check.check(db, transport, FakeClock(FRI_0600), "0.0.0", Config())
    assert not update_check.check(db, transport, FakeClock(FRI_0600), "0.3.1", Config(update_check=False))
    assert transport.requests == [] and line(db, "0.0.0")["text"] == "not checked for a dev build"


def test_DR25_the_beta_channel_reads_the_list_and_the_stable_channel_only_latest(db):
    transport, clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
    update_check.check(db, transport, clock, "0.3.1", Config())
    assert [r.url.rsplit("/", 1)[-1] for r in transport.requests] == ["latest"]
    update_check.write_meta(db, {"checked_ms": None, "etag": None})
    update_check.check(db, transport, clock, "0.3.1", Config(update_channel="beta"))
    assert transport.requests[-1].url.endswith("/releases?per_page=20")
    assert update_check.read_meta(db)["prerelease"] == "1"


def test_a_channel_with_no_release_clears_the_stored_one_and_a_new_release_gets_fresh_tries(db):
    transport, clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
    update_check.write_meta(db, {"tag": "v0.3.9", "download_failed": "checksum mismatch", "download_tried_ms": 1})
    update_check.check(db, transport, clock, "0.3.1", Config())
    assert "download_failed" not in update_check.read_meta(db)
    transport.serve_bytes(f"{GITHUB_REPO}/releases/latest", b'{"tag_name": "v0.5.0-rc1", "html_url": "x"}')
    clock.set(FRI_0600 + 24 * HOUR)
    update_check.check(db, transport, clock, "0.3.1", Config())
    assert "tag" not in update_check.read_meta(db)
    assert line(db, now=clock.now_ms())["text"] == "up to date · checked 06:00"


def test_FM52_a_beta_answer_that_is_not_a_list_is_github_unavailable(db):
    transport = FakeTransport.serve_github()
    transport.serve_bytes(f"{GITHUB_REPO}/releases", b'{"message": "Not Found"}')
    update_check.write_meta(db, {"tag": "v0.4.0"})
    update_check.check(db, transport, FakeClock(FRI_0600), "0.3.1", Config(update_channel="beta"))
    assert line(db, config=Config(update_channel="beta"))["text"] == "not checked: GitHub unavailable since Fri 06:00"
    assert update_check.read_meta(db)["tag"] == "v0.4.0"                 # the last answer is kept
