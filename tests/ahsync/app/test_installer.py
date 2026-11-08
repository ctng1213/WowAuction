"""Tests for ahsync/app/installer.py: install a verified update on quit, and settle the result at the next start
(SPEC §3.7.9; DR22, FM54, FM55)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync.app import installer
from ahsync.app.update_check import read_meta, read_state, write_meta
from ahsync.config import Config
from ahsync.db.sqlite import Database
from ahsync.engine.updates import update_text

NOW = 1_762_000_000_000
SETUP = "AHSync-0.4.0-windows-x64-setup.exe"


def asset(version: str) -> str:
    """Windows' asset name for a version."""
    return f"AHSync-{version}-windows-x64-setup.exe"


@pytest.fixture
def waiting(tmp_path):
    """A database with DR20's verified 0.4.0 installer waiting in <data>/updates/, running 0.3.1."""
    data = tmp_path / "data"
    folder = data / "updates"
    folder.mkdir(parents=True)
    (folder / SETUP).write_bytes(b"installer")
    db = Database.open(data / "ahsync.db", NOW, None)
    write_meta(db, {"tag": "v0.4.0", "url": "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0",
                    "checked_ms": NOW, "downloaded": "0.4.0", "downloaded_path": str(folder / SETUP)})
    yield db, data, folder
    db.close()


def line(db: Database, version: str) -> dict:
    """The Update line as the Status card shows it."""
    return update_text(read_state(db, version, Config()), NOW + 60_000, 0)


def test_DR22_the_install_starts_after_the_stop_and_the_next_start_records_it(waiting):
    db, data, folder = waiting
    applied: list[Path] = []
    assert installer.install(db, "0.3.1", Config(), folder, asset, applied.append)
    assert applied == [folder / SETUP] and read_meta(db)["pending"] == "0.4.0"
    forgotten = []
    assert installer.settle(db, data, "0.4.0", NOW + 60_000, lambda: forgotten.append(True)) == "updated"   # 0.4.0 runs
    meta = read_meta(db)
    assert ("pending" not in meta and "downloaded" not in meta and meta["updated_to"] == "0.4.0"
            and forgotten == [True] and list(folder.iterdir()) == [])
    assert line(db, "0.4.0") == {"text": "Updated to AHSync 0.4.0 · today 12:27", "url": None, "pill": None,
                                 "restart": False, "class": ""}
    assert installer.settle(db, data, "0.4.0", NOW + 120_000, lambda: None) is None      # once only


def test_DR22_an_installer_still_held_is_left_and_removed_at_a_later_start(waiting, monkeypatch):
    db, data, folder = waiting
    write_meta(db, {"pending": "0.4.0"})
    real_unlink = Path.unlink

    def held(self, missing_ok=False):
        """The finishing installer process still holds its file (Windows)."""
        if self.name == SETUP:
            raise PermissionError("in use")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", held)
    assert installer.settle(db, data, "0.4.0", NOW, lambda: None) == "updated"    # the start goes on
    assert [p.name for p in folder.iterdir()] == [SETUP]
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert installer.settle(db, data, "0.4.0", NOW, lambda: None) is None
    assert list(folder.iterdir()) == []                                    # the following start removes it


def test_FM55_an_install_that_left_the_old_version_says_so_and_tries_again_tomorrow(waiting):
    db, data, folder = waiting
    assert installer.install(db, "0.3.1", Config(), folder, asset, lambda path: None)
    assert installer.settle(db, data, "0.3.1", NOW, lambda: None) == "failed"      # still 0.3.1 at the next start
    meta = read_meta(db)
    assert "pending" not in meta and "downloaded" not in meta and meta["download_tried_ms"] == str(NOW)
    assert line(db, "0.3.1")["text"] == "Update to 0.4.0 failed · still on 0.3.1 · tries again tomorrow"
    assert line(db, "0.3.1")["class"] == "amber" and list(folder.iterdir()) == []


def test_FM54_an_app_that_cannot_replace_itself_installs_nothing(waiting):
    db, _data, folder = waiting
    write_meta(db, {"cant_replace": "1"})
    assert not installer.install(db, "0.3.1", Config(), folder, asset, lambda path: pytest.fail("nothing may start"))
    assert "pending" not in read_meta(db)
    assert line(db, "0.3.1") == {"text": "Update downloaded · AHSync can't replace itself here; install it from the "
                                 "release page", "url": "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0",
                                 "pill": None, "restart": False, "class": "amber"}


def test_nothing_waits_or_it_cannot_start_and_the_app_simply_quits(waiting):
    db, _data, folder = waiting
    never = lambda path: pytest.fail("nothing may start")                         # noqa: E731
    assert not installer.install(db, "0.3.1", Config(auto_update=False), folder, asset, never)
    for error in (OSError("blocked"), ValueError("odd"), NotImplementedError()):   # any failure: the app goes on
        def refused(path: Path, error=error) -> None:
            """The installer could not be started."""
            raise error
        assert not installer.install(db, "0.3.1", Config(), folder, asset, refused)
        assert "pending" not in read_meta(db)
    (folder / SETUP).unlink()
    assert installer.waiting_install(db, "0.3.1", Config(), folder, asset) is None   # the file is gone


def test_DR22_only_a_newer_settled_download_in_updates_ever_installs(waiting, tmp_path):
    db, _data, folder = waiting
    never = lambda path: pytest.fail("nothing may start")                         # noqa: E731
    assert installer.waiting_install(db, "0.3.1", Config(), folder, asset) == folder / SETUP
    assert not installer.install(db, "0.4.0", Config(), folder, asset, never)      # already 0.4.0: not newer
    write_meta(db, {"pending": "0.4.0"})                                            # the last install isn't settled
    assert not installer.install(db, "0.3.1", Config(), folder, asset, never)
    write_meta(db, {"pending": None})
    elsewhere = tmp_path / SETUP
    elsewhere.write_bytes(b"not ours")
    write_meta(db, {"downloaded_path": str(elsewhere)})                             # a path outside updates/
    assert not installer.install(db, "0.3.1", Config(), folder, asset, never)
    write_meta(db, {"downloaded_path": str(folder / SETUP)})
    assert not installer.install(db, "0.3.1", Config(), folder, lambda v: f"other-{v}.exe", never)   # not the asset
