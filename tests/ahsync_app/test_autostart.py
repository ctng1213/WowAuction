"""Tests for ahsync_app/autostart.py: start at login's shared decisions (SPEC §3.7.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from ahsync_app import autostart
from ahsync_app.platform.macos import MacPlatform
from ahsync_app.platform.windows import WindowsPlatform

TRANSLOCATED_EXE = Path("/private/var/folders/x/AppTranslocation/3F2A/d/AHSync.app/Contents/MacOS/AHSync")
APP_EXE = Path("/Applications/AHSync.app/Contents/MacOS/AHSync")


class RunKey:
    """A Run key in memory that can be made to fail."""

    def __init__(self, fail: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.fail = fail

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, data: str) -> None:
        if self.fail:
            raise OSError(5, "Access is denied")
        self.values[name] = data

    def delete(self, name: str) -> None:
        if self.fail:
            raise OSError(5, "Access is denied")
        self.values.pop(name, None)


def test_DR11_a_translocated_app_is_refused_before_anything_is_written(tmp_path):
    platform = MacPlatform(tmp_path)
    with pytest.raises(autostart.BadLocation):
        autostart.set_enabled(platform, TRANSLOCATED_EXE, True)
    assert autostart.request(platform, TRANSLOCATED_EXE, True) == (autostart.BAD_LOCATION, False, "")
    assert not (tmp_path / "Library").exists()
    line, translocated = autostart.facts(platform, TRANSLOCATED_EXE)
    assert translocated and line == {"enabled": False, "available": False,
                                     "note": "not set: Move AHSync to Applications, then turn it on again"}


def test_SEC14_a_request_turns_it_on_and_off_and_answers_the_real_state():
    run_key = RunKey()
    platform = WindowsPlatform({}, Path("C:/Users/u"), run_key)
    exe = Path(r"C:\Users\u\AppData\Local\Programs\AHSync\AHSync.exe")
    assert autostart.request(platform, exe, True) == (autostart.SET, True, "") and "AHSync" in run_key.values
    assert autostart.request(platform, exe, False) == (autostart.SET, False, "") and run_key.values == {}
    assert autostart.facts(platform, exe) == ({"enabled": False, "available": True, "note": ""}, False)


def test_FM51_a_write_that_fails_is_not_set_with_the_real_state(caplog):
    run_key = RunKey(fail=True)
    platform = WindowsPlatform({}, Path("C:/Users/u"), run_key)
    assert autostart.request(platform, Path("C:/AHSync.exe"), True) == (autostart.NOT_SET, False, "OSError")
    run_key.fail, run_key.values = True, {"AHSync": '"C:/AHSync.exe" --autostart'}
    assert autostart.request(platform, Path("C:/AHSync.exe"), False) == (autostart.NOT_SET, True, "OSError")
    assert "start at login not set: OSError" in caplog.text and "Access is denied" not in caplog.text


def test_self_heal_points_an_enabled_entry_at_the_moved_app_and_leaves_an_off_one_off(tmp_path):
    platform = MacPlatform(tmp_path)
    autostart.self_heal(platform, APP_EXE)
    assert not platform.autostart_enabled()            # off stays off
    platform.autostart_enable(Path("/Users/u/Downloads/AHSync.app/Contents/MacOS/AHSync"))
    autostart.self_heal(platform, APP_EXE)
    assert APP_EXE.as_posix() in platform.agent_file().read_text(encoding="utf-8")
    autostart.self_heal(platform, TRANSLOCATED_EXE)    # never points the entry at a translocated copy
    assert "AppTranslocation" not in platform.agent_file().read_text(encoding="utf-8")


def test_self_heal_logs_a_failed_rewrite(caplog):
    run_key = RunKey()
    run_key.values["AHSync"] = '"C:/old/AHSync.exe" --autostart'
    run_key.fail = True
    autostart.self_heal(WindowsPlatform({}, Path("C:/Users/u"), run_key), Path("C:/new/AHSync.exe"))
    assert "start at login not refreshed: OSError" in caplog.text


def test_a_platform_without_the_entry_reads_as_off():
    from ahsync_app.platform.base import Platform

    class Bare(Platform):
        def run_location_problem(self, exe: Path) -> str | None:
            return None

    assert not autostart.is_enabled(Bare())
    assert autostart.request(Bare(), APP_EXE, True) == (autostart.NOT_SET, False, "NotImplementedError")
