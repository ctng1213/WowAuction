"""Tests for app/updater.py: the download half of automatic updates (SPEC §3.7.9; DR20, DR21, DR24–DR26, FM53)."""
from __future__ import annotations

import calendar
import hashlib

import pytest

from ahsync.app import update_check, updater
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.updates import update_text
from tests.support.fake_clock import FakeClock
from tests.support.fake_transport import GITHUB_DOWNLOADS, GITHUB_FIXTURES, GITHUB_REPO, FakeTransport

FRI_0600 = calendar.timegm((2026, 11, 6, 6, 0, 0)) * 1000
HOUR = 3_600_000
WINDOWS = "AHSync-{}-windows-x64-setup.exe".format
MAC = "AHSync-{}-macos-arm64.zip".format
EXE = f"{GITHUB_DOWNLOADS}/v0.4.0/AHSync-0.4.0-windows-x64-setup.exe"
SUMS = f"{GITHUB_DOWNLOADS}/v0.4.0/SHA256SUMS"


class World:
    """A database, the GitHub fixtures and a data folder at Fri 06:00."""

    def __init__(self, tmp_path, version: str = "0.3.1", config: Config = Config(), asset=WINDOWS) -> None:
        self.db = Database.open(tmp_path / "ahsync.db", FRI_0600, None)
        self.transport, self.clock = FakeTransport.serve_github(), FakeClock(FRI_0600)
        self.data, self.version, self.config, self.asset = tmp_path / "data", version, config, asset
        self.verified: list = []

    def verify(self, path):
        self.verified.append(path.name)
        return None

    def check_and_download(self) -> bool:
        update_check.check(self.db, self.transport, self.clock, self.version, self.config)
        return updater.download(self.db, self.transport, self.clock, self.data, self.version, self.config,
                                self.asset, self.verify)

    def line(self) -> dict:
        return update_text(update_check.read_state(self.db, self.version, self.config), self.clock.now_ms(), 0)

    def files(self) -> list[str]:
        folder = self.data / "updates"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def downloads(self) -> list[str]:
        return [r.url.rsplit("/", 1)[-1] for r in self.transport.requests if "/releases/download/" in r.url]


@pytest.fixture
def world(tmp_path):
    made = []

    def make(**kwargs) -> World:
        made.append(World(tmp_path / str(len(made)), **kwargs))
        return made[-1]

    yield make
    for w in made:
        w.db.close()


def test_DR20_the_right_asset_is_downloaded_verified_and_waits(world):
    w = world()
    assert w.check_and_download()
    assert w.downloads() == ["SHA256SUMS", "AHSync-0.4.0-windows-x64-setup.exe"] and w.verified == [WINDOWS("0.4.0")]
    path = w.data / "updates" / WINDOWS("0.4.0")
    assert w.files() == [WINDOWS("0.4.0")]
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(
        (GITHUB_FIXTURES / WINDOWS("0.4.0")).read_bytes()).digest()
    meta = update_check.read_meta(w.db)
    assert (meta["downloaded"], meta["downloaded_path"]) == ("0.4.0", str(path))
    assert w.line() == {"text": "AHSync 0.4.0 downloaded · installs at the next start · you run 0.3.1",
                        "url": "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0",
                        "pill": "update ready · 0.4.0", "restart": True, "class": "amber"}
    assert not updater.download(w.db, w.transport, w.clock, w.data, w.version, w.config, w.asset, w.verify)
    mac = world(asset=MAC)
    mac.check_and_download()
    assert mac.downloads() == ["SHA256SUMS", MAC("0.4.0")] and mac.files() == [MAC("0.4.0")]


def test_DR21_auto_update_off_only_tells(world):
    w = world(config=Config(auto_update=False))
    assert not w.check_and_download()
    assert w.downloads() == [] and w.files() == []
    assert (w.line()["text"], w.line()["pill"], w.line()["restart"]) == (
        "AHSync 0.4.0 is available · you run 0.3.1", "update available", False)


@pytest.mark.parametrize("version, latest", [("0.3.1", "v0.3.1"), ("0.3.1", "v0.3.0"), ("0.3.1", "v0.4.0-rc1"),
                                             ("0.0.0", "v0.4.0")])
def test_DR24_never_an_equal_older_or_suffixed_version_never_on_a_dev_build(world, version, latest):
    w = world(version=version)
    w.transport.serve_bytes(f"{GITHUB_REPO}/releases/latest", f'{{"tag_name": "{latest}", "html_url": "p"}}'.encode())
    assert not w.check_and_download()
    assert w.downloads() == [] and w.files() == []
    assert w.line()["text"] == ("not checked for a dev build" if version == "0.0.0" else "up to date · checked 06:00")
    if version == "0.0.0":
        assert w.transport.requests == []


def test_DR25_the_beta_channel_downloads_the_pre_release(world):
    w = world(config=Config(update_channel="beta"))
    w.check_and_download()
    assert w.files() == [WINDOWS("0.4.0")]
    assert w.line()["text"] == "AHSync 0.4.0 (beta) downloaded · installs at the next start · you run 0.3.1"


def test_DR26_a_beta_install_on_0_4_0_and_one_that_left_the_beta_download_nothing(world):
    on_beta = world(version="0.4.0", config=Config(update_channel="beta"))
    assert not on_beta.check_and_download() and on_beta.files() == []
    assert on_beta.line()["text"] == "up to date · checked 06:00"
    left = world(version="0.4.1")
    update_check.write_meta(left.db, {"left_beta": "1"})
    assert not left.check_and_download() and left.files() == []
    assert left.line()["text"] == "up to date · stays on 0.4.1 until a newer stable release"


@pytest.mark.parametrize("fault, reason", [
    ("cut", "download cut off"), ("hash", "checksum mismatch"), ("unlisted", "not in SHA256SUMS"),
    ("offline", "download cut off"), ("sums 404", "download cut off"), ("signature", "not signed by the AHSync developer"),
])
def test_FM53_a_failed_download_leaves_nothing_and_tries_again_tomorrow(world, fault, reason):
    w = world()
    sums = (GITHUB_FIXTURES / "SHA256SUMS").read_text(encoding="utf-8")
    if fault == "cut":
        w.transport.truncate(EXE, 0.5)                                    # 1,000 of 2,000 bytes, then closed
    elif fault == "hash":
        w.transport.serve_bytes(SUMS, sums.replace(sums[:8], "00000000").encode())
    elif fault == "unlisted":
        w.transport.serve_bytes(SUMS, "\n".join(l for l in sums.splitlines() if "windows" not in l).encode())
    elif fault == "offline":
        w.transport.network_error(EXE)
    elif fault == "sums 404":
        w.transport.remove(SUMS)
    else:
        w.verify = lambda _path: "not signed by the AHSync developer"
    assert w.check_and_download()
    assert w.files() == [] and "downloaded" not in update_check.read_meta(w.db)
    assert w.line() == {"text": f"Update to 0.4.0 failed: {reason} · still on 0.3.1 · tries again tomorrow",
                        "url": None, "pill": None, "restart": False, "class": "amber"}
    w.transport.clear_faults()
    w.transport.serve_github(w.transport)
    w.verify = lambda _path: None
    w.clock.set(FRI_0600 + 23 * HOUR)
    assert not updater.download(w.db, w.transport, w.clock, w.data, w.version, w.config, w.asset, w.verify)
    w.clock.set(FRI_0600 + 24 * HOUR)                                     # Sat 06:00: tried again
    assert updater.download(w.db, w.transport, w.clock, w.data, w.version, w.config, w.asset, w.verify)
    assert w.files() == [WINDOWS("0.4.0")]


def test_a_newer_download_replaces_an_older_one_and_sums_lines_parse_both_forms(world, tmp_path):
    w = world()
    (w.data / "updates").mkdir(parents=True)
    (w.data / "updates" / WINDOWS("0.3.9")).write_bytes(b"old")
    w.check_and_download()
    assert w.files() == [WINDOWS("0.4.0")]
    assert updater.parse_sums(f"{'A' * 64} *x.zip\nshort  y\n\n{'b' * 64}  z.exe") == {"x.zip": "a" * 64,
                                                                                    "z.exe": "b" * 64}


def test_an_older_file_still_in_use_is_left_for_later(world, monkeypatch, caplog):
    w = world()
    (w.data / "updates").mkdir(parents=True)
    stale = w.data / "updates" / WINDOWS("0.3.9")
    stale.write_bytes(b"old")
    real_unlink = type(stale).unlink

    def busy(self, missing_ok=False):
        if self.name == stale.name:
            raise PermissionError("in use")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(stale), "unlink", busy)
    with caplog.at_level("INFO", logger="ahsync.update"):
        w.check_and_download()
    assert w.files() == [WINDOWS("0.3.9"), WINDOWS("0.4.0")] and "still in use" in caplog.text


def test_DR30_only_what_the_channel_still_offers_waits_to_install(world):
    beta = world(config=Config(update_channel="beta"))
    beta.check_and_download()
    assert beta.files() == [WINDOWS("0.4.0")] and update_check.read_meta(beta.db)["prerelease"] == "1"
    beta.config = Config(update_channel="stable")                     # switched as the endpoint does it
    update_check.write_meta(beta.db, {"checked_ms": None, "etag": None, "tag": None, "url": None, "prerelease": None,
                                      "left_beta": "1"})
    assert beta.line()["restart"] is False                            # at once, before the next job
    beta.transport.serve_bytes(f"{GITHUB_REPO}/releases/latest", b'{"tag_name": "v0.3.1", "html_url": "p"}')
    beta.check_and_download()
    assert beta.files() == [] and "downloaded" not in update_check.read_meta(beta.db)
    assert beta.line()["text"] == "up to date · checked 06:00"

    off = world()
    off.check_and_download()
    off.config = Config(auto_update=False)
    assert off.line()["text"] == "AHSync 0.4.0 is available · you run 0.3.1" and off.line()["restart"] is False
    off.check_and_download()
    assert off.files() == []

    withdrawn = world(config=Config(update_channel="beta"))
    withdrawn.check_and_download()
    withdrawn.transport.serve_bytes(f"{GITHUB_REPO}/releases", b'[{"tag_name": "v0.3.1", "html_url": "p"}]')
    withdrawn.clock.set(FRI_0600 + 24 * HOUR)
    withdrawn.check_and_download()
    assert withdrawn.files() == [] and withdrawn.line()["text"] == "up to date · checked 06:00"


def test_DR30_a_newer_release_that_fails_takes_the_waiting_one_with_it(world):
    w = world()
    w.check_and_download()
    newer_latest = (GITHUB_FIXTURES / "latest.json").read_bytes().replace(b"0.4.0", b"0.5.0")
    w.transport.serve_bytes(f"{GITHUB_REPO}/releases/latest", newer_latest)
    w.clock.set(FRI_0600 + 24 * HOUR)
    w.check_and_download()                                            # v0.5.0's files aren't served: cut off
    assert w.files() == [] and w.line()["text"] == \
        "Update to 0.5.0 failed: download cut off · still on 0.3.1 · tries again tomorrow"


def test_DR30_a_signature_check_that_raises_keeps_nothing_and_waits_a_day(world):
    w = world()

    def broken(_path):
        raise OSError("codesign not found")

    w.verify = broken
    assert w.check_and_download()
    assert w.files() == [] and w.line()["text"] == \
        "Update to 0.4.0 failed: could not be verified · still on 0.3.1 · tries again tomorrow"
    w.clock.set(FRI_0600 + 10 * 60_000)                               # the next job, 10 minutes later
    requests = len(w.transport.requests)
    assert not updater.download(w.db, w.transport, w.clock, w.data, w.version, w.config, w.asset, w.verify)
    assert len(w.transport.requests) == requests


def test_update_check_off_downloads_nothing_even_with_a_stored_release(world):
    w = world()
    update_check.write_meta(w.db, {"tag": "v0.4.0", "checked_ms": FRI_0600})
    assert not updater.download(w.db, w.transport, w.clock, w.data, w.version, Config(update_check=False), w.asset,
                                w.verify)
    assert w.transport.requests == []


def test_a_folder_that_cannot_be_made_is_a_failed_try(world, monkeypatch):
    w = world()
    update_check.check(w.db, w.transport, w.clock, w.version, w.config)
    real_mkdir = type(w.data).mkdir

    def refused(self, *args, **kwargs):
        if self.name == "updates":
            raise PermissionError("denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(type(w.data), "mkdir", refused)
    assert updater.download(w.db, w.transport, w.clock, w.data, w.version, w.config, w.asset, w.verify)
    assert w.line()["text"].startswith("Update to 0.4.0 failed: download cut off")
