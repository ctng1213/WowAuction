"""Tests for ahsync_app/platform/macos.py: the macOS answers, on any OS (SPEC §3.7.2)."""
from __future__ import annotations

from pathlib import Path

from ahsync_app.platform.base import TRANSLOCATED
from ahsync_app.platform.macos import MacPlatform


def test_DR1_the_per_user_folders_are_under_application_support():
    dirs = MacPlatform(Path("/Users/u")).app_dirs()
    assert dirs.data_dir.as_posix() == "/Users/u/Library/Application Support/AHSync/data"
    assert dirs.config_file.as_posix() == "/Users/u/Library/Application Support/AHSync/config.toml"


def test_the_wow_candidates_put_forever_s_folder_first():
    candidates = [p.as_posix() for p in MacPlatform(Path("/Users/u")).wow_candidates()]
    assert candidates[0] == "/Applications/World of Warcraft/_classic_beta_"
    assert all(c.startswith("/Applications/World of Warcraft/") for c in candidates)


def test_the_key_file_is_made_readable_by_the_user_only():
    calls = []
    MacPlatform(Path("/Users/u"), chmod=lambda path, mode: calls.append((path, mode))).restrict_file(Path("/k"))
    assert calls == [(Path("/k"), 0o600)]


def test_a_translocated_app_is_a_location_problem_and_applications_is_not():
    platform = MacPlatform(Path("/Users/u"))
    exe = "/private/var/folders/x1/abc/T/AppTranslocation/0F2E/d/AHSync.app/Contents/MacOS/AHSync"
    assert platform.run_location_problem(Path(exe)) == TRANSLOCATED
    assert platform.run_location_problem(Path("/Applications/AHSync.app/Contents/MacOS/AHSync")) is None
    assert platform.run_location_problem(Path("/Users/u/Applications/AHSync.app/Contents/MacOS/AHSync")) is None


APP_EXE = "/Applications/AHSync.app/Contents/MacOS/AHSync"


def test_DR10_start_at_login_is_the_launchagent_file_and_launchctl_never_runs(tmp_path, monkeypatch):
    import plistlib
    import subprocess

    def no_launchctl(*_args, **_kwargs):
        raise AssertionError("launchctl must not run")

    monkeypatch.setattr(subprocess, "run", no_launchctl)
    home = tmp_path / "Users" / "u"
    platform = MacPlatform(home)
    agent = home / "Library" / "LaunchAgents" / "com.wowauction.ahsync.plist"
    platform.autostart_enable(Path(APP_EXE))
    with agent.open("rb") as f:
        assert plistlib.load(f) == {"Label": "com.wowauction.ahsync", "ProgramArguments": [APP_EXE, "--autostart"],
                                    "RunAtLoad": True}
    assert platform.autostart_enabled()

    before = agent.stat().st_mtime_ns
    agent.touch()
    touched = agent.stat().st_mtime_ns
    platform.autostart_enable(Path(APP_EXE))           # the same bytes: left alone
    assert agent.stat().st_mtime_ns == touched and touched >= before

    moved = "/Users/u/Applications/AHSync.app/Contents/MacOS/AHSync"
    platform.autostart_enable(Path(moved))
    with agent.open("rb") as f:
        assert plistlib.load(f)["ProgramArguments"] == [moved, "--autostart"]
    assert sorted(p.name for p in agent.parent.iterdir()) == ["com.wowauction.ahsync.plist"]

    platform.autostart_disable()
    assert not agent.exists() and not platform.autostart_enabled()
    platform.autostart_disable()                       # nothing to remove: fine


def test_FM51_a_launchagents_folder_that_cannot_be_written_leaves_no_plist_and_no_temp(tmp_path, monkeypatch):
    import os

    home = tmp_path / "Users" / "u"
    platform = MacPlatform(home)

    def refused(*_args):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", refused)
    try:
        platform.autostart_enable(Path(APP_EXE))
        raise AssertionError("the write should have failed")
    except PermissionError:
        pass
    assert not platform.autostart_enabled() and list((home / "Library" / "LaunchAgents").iterdir()) == []


def test_DR10_from_source_the_agent_runs_python_with_the_script(tmp_path):
    import plistlib

    platform = MacPlatform(tmp_path)
    platform.autostart_enable(Path("/usr/bin/python3"), ("/Users/u/WowAuction/ahsync_app/__main__.py",))
    with platform.agent_file().open("rb") as f:
        assert plistlib.load(f)["ProgramArguments"] == ["/usr/bin/python3", "/Users/u/WowAuction/ahsync_app/__main__.py",
                                                         "--autostart"]


def test_DR20_macos_takes_the_app_zip():
    assert MacPlatform(Path("/Users/u")).update_asset_name("0.4.0") == "AHSync-0.4.0-macos-arm64.zip"
    assert MacPlatform(Path("/Users/u")).app_dirs().shown == "~/Library/Application Support/AHSync"


def test_FM58_macos_tls_falls_back_to_the_system_ca_bundle():
    assert MacPlatform(Path("/Users/u")).ca_bundle() == Path("/etc/ssl/cert.pem")
