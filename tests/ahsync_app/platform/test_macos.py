"""Tests for ahsync_app/platform/macos.py: the macOS answers, on any OS (SPEC §3.7.2)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ahsync_app.platform.base import TRANSLOCATED
from ahsync_app.platform.macos import MacPlatform


def test_DR1_the_per_user_folders_are_under_application_support():
    dirs = MacPlatform(Path("/Users/u")).app_dirs()
    assert dirs.data_dir.as_posix() == "/Users/u/Library/Application Support/AHSync/data"
    assert dirs.config_file.as_posix() == "/Users/u/Library/Application Support/AHSync/config.toml"


def test_the_wow_candidates_put_forever_s_folder_first():
    candidates = [p.as_posix() for p in MacPlatform(Path("/Users/u")).wow_candidates()]
    assert candidates == ["/Applications/World of Warcraft/_forever_", "/Applications/World of Warcraft/_classic_beta_"]
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


class FakeCodesign:
    """Records commands; `ditto` unpacks an AHSync.app folder, `codesign` answers from the given Team IDs."""

    def __init__(self, teams: dict[str, str | None], valid: bool = True) -> None:
        self.teams, self.valid, self.calls, self.spawned = teams, valid, [], []

    def run(self, args):
        """A finished command."""
        from ahsync_app.platform.base import Ran
        self.calls.append(list(args))
        if args[0] == "ditto":
            (Path(args[3]) / "AHSync.app" / "Contents").mkdir(parents=True, exist_ok=True)
            return Ran(0)
        if args[:2] == ["codesign", "--verify"]:
            return Ran(0 if self.valid else 1, err="" if self.valid else "invalid signature")
        team = self.teams.get(Path(args[2]).parent.name)                 # keyed by the bundle's folder
        return Ran(0, err=f"Executable=…\nTeamIdentifier={team or 'not set'}\n")

    def spawn(self, args, log=None):
        """A detached start, recorded."""
        self.spawned.append((list(args), log))


def mac_with(tmp_path, runner, writable=True):
    """A MacPlatform whose app runs from tmp/Applications/AHSync.app, over the fake runner."""
    from ahsync_app.platform.base import Processes
    exe = tmp_path / "Applications" / "AHSync.app" / "Contents" / "MacOS" / "AHSync"
    exe.parent.mkdir(parents=True)
    updates = tmp_path / "data" / "updates"
    updates.mkdir(parents=True)
    zip_path = updates / "AHSync-0.4.0-macos-arm64.zip"
    zip_path.write_bytes(b"zip")
    platform = MacPlatform(tmp_path, chmod=lambda p, m: None, processes=Processes(runner.run, runner.spawn, 4242),
                           writable=lambda folder: writable)
    return platform, exe, zip_path


def test_DR23_the_zip_is_unpacked_by_ditto_and_must_carry_the_running_app_s_team_id(tmp_path):
    from ahsync_app.platform.base import NOT_SIGNED
    ours = FakeCodesign({"Applications": "ABCDE12345", "updates": "ABCDE12345"})
    platform, exe, zip_path = mac_with(tmp_path, ours)
    assert platform.verify_update(zip_path, exe) is None
    assert ours.calls[0] == ["ditto", "-xk", str(zip_path), str(zip_path.parent)]          # never zipfile
    assert (zip_path.parent / "AHSync.app").is_dir()
    for runner in (FakeCodesign({"Applications": "ABCDE12345", "updates": "ZZZZZ99999"}),   # someone else's
                   FakeCodesign({"Applications": "ABCDE12345", "updates": "ABCDE12345"}, valid=False),
                   FakeCodesign({"Applications": "ABCDE12345", "updates": None})):          # ad hoc
        other, exe2, zip2 = mac_with(tmp_path / str(len(runner.teams)) / str(runner.valid) / str(runner.teams["updates"]), runner)
        assert other.verify_update(zip2, exe2) == NOT_SIGNED
        assert not (zip2.parent / "AHSync.app").exists()                                    # the refused app is removed


def test_DR23_the_swap_helper_is_written_and_started_detached_over_the_bundle_from_the_executable(tmp_path):
    runner = FakeCodesign({})
    platform, exe, zip_path = mac_with(tmp_path, runner)
    platform.apply_update(zip_path, exe)
    script = zip_path.parent / "swap.sh"
    assert script.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert runner.spawned == [(["/bin/sh", str(script), "4242", str(tmp_path / "Applications" / "AHSync.app"),
                                str(zip_path.parent / "AHSync.app")], zip_path.parent / "swap.log")]


def test_FM54_a_bundle_in_a_folder_the_user_cannot_write_cannot_replace_itself(tmp_path):
    platform, exe, _zip = mac_with(tmp_path, FakeCodesign({}), writable=False)
    assert platform.can_replace(exe) is False
    assert mac_with(tmp_path / "w", FakeCodesign({}))[0].can_replace(exe) is True


def test_an_update_that_worked_forgets_the_previous_app(tmp_path):
    platform, exe, _zip = mac_with(tmp_path, FakeCodesign({}))
    previous = tmp_path / "Applications" / "AHSync.app.previous"
    (previous / "Contents").mkdir(parents=True)
    platform.forget_previous(exe)
    assert not previous.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the swap helper is a /bin/sh script (macOS)")
@pytest.mark.parametrize("fault", ["none", "open", "move"])
def test_FM55_the_swap_helper_puts_the_previous_app_back_when_the_move_or_the_open_fails(tmp_path, fault):
    from ahsync_app.platform.macos import SWAP
    apps, fakes = tmp_path / "Applications", tmp_path / "bin"
    bundle, new = apps / "AHSync.app", tmp_path / "updates" / "AHSync.app"
    for folder, version in ((bundle, "0.3.1"), (new, "0.4.0")):
        folder.mkdir(parents=True)
        (folder / "version").write_text(version)
    fakes.mkdir()
    opened = tmp_path / "opened"
    (fakes / "open").write_text(f'#!/bin/sh\necho "$1" >> "{opened}"\n'
                                + ('[ "$(cat "$1/version")" = 0.4.0 ] && exit 1\n' if fault == "open" else "") + "exit 0\n")
    (fakes / "open").chmod(0o755)
    if fault == "move":
        new.rename(tmp_path / "updates" / "gone")                         # the new app's move fails: nothing there
    script = tmp_path / "swap.sh"
    script.write_text(SWAP)
    done = subprocess.run(["/bin/sh", str(script), "999999", str(bundle), str(new)], env={"PATH": f"{fakes}:/bin:/usr/bin"},
                          capture_output=True, text=True, timeout=30)
    running = (bundle / "version").read_text()
    if fault == "none":
        assert done.returncode == 0 and running == "0.4.0" and (apps / "AHSync.app.previous").is_dir()
    else:
        assert done.returncode == 1 and running == "0.3.1"                # the previous app is back
        assert opened.read_text().splitlines()[-1] == str(bundle)        # and opened
