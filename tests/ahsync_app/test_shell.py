"""Tests for ahsync_app/shell.py: the app's lifecycle around run() (SPEC §3.7.1, §3.7.6, §3.7.7)."""
from __future__ import annotations

import io
import time
from pathlib import Path

from ahsync.__main__ import LOCK_FILE, InstanceLock, Runtime
from ahsync_app import shell
from ahsync_app.platform.base import AppDirs, Platform
from ahsync_app.platform.macos import MacPlatform
from tests.support.fake_clock import FakeClock

BUNDLE = {"AHProfit/AHProfit.toc": "toc\n", "AHProfit/Core/Adapter.lua": "-- a\n", "AHProfit/UI/MainWindow.lua": "-- w\n",
          "AHProfit_Data/AHProfit_Data.toc": "toc\n", "AHProfit_Data/MarketIntelligence.lua": "-- p\n",
          "AHProfit_Data/PortfolioPlan.lua": "-- p\n"}


def write(root: Path, files: dict[str, str]) -> None:
    """Write text files under root, creating folders."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


class FolderPlatform(Platform):
    """A platform whose per-user folder is tmp/Local/AHSync on any OS (the real Windows paths: test_windows.py)."""

    def __init__(self, root: Path) -> None:
        self.root = root / "Local" / "AHSync"

    def app_dirs(self) -> AppDirs:
        return AppDirs(self.root / "config.toml", self.root / "data")


class FakeApp:
    """A fake run(), browser and dashboard port. run() records its arguments, then waits for the stop event,
    for `until()` to hold, or returns at once."""

    def __init__(self, tmp_path: Path, platform=None, until=None, port_after: int = 0) -> None:
        self.tmp = tmp_path
        self.platform = platform or FolderPlatform(tmp_path)
        self.until = until
        self.port_after = port_after          # the dashboard answers from this probe on
        self.calls: list[tuple[Path, Path, bool]] = []
        self.opened: list[str] = []
        self.probes = 0
        self.out = io.StringIO()
        write(tmp_path / "bundle" / "addon", BUNDLE)

    def run(self, data_dir: Path, config: Path, runtime: Runtime, held_lock=None) -> int:
        """The fake ahsync run()."""
        self.calls.append((data_dir, config, held_lock is not None))
        deadline = time.monotonic() + 5
        while self.until is not None and not self.until(data_dir) and not runtime.should_stop():
            assert time.monotonic() < deadline, "the shell never did what the fake run waits for"
            time.sleep(0.002)
        return 0

    def port_open(self, port: int) -> bool:
        """The fake dashboard probe."""
        self.probes += 1
        return self.probes > self.port_after

    def world(self) -> shell.ShellWorld:
        """The shell's world over these fakes."""
        return shell.ShellWorld(self.platform, self.run,
                                lambda stop: Runtime({}, self.tmp, None, FakeClock(), stop, self.out),
                                self.opened.append, self.port_open, lambda _s: time.sleep(0.001),
                                self.tmp / "bundle" / "addon", {}, self.tmp, self.out)

    def configure(self, wow: Path) -> None:
        """Write the app's config.toml pointing at a WoW folder."""
        config = self.platform.app_dirs().config_file
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f'wow_path = "{wow.as_posix()}"\n', encoding="utf-8")


def test_DR1_the_shell_runs_ahsync_on_the_per_user_folders_and_creates_them(tmp_path):
    app = FakeApp(tmp_path)
    app.configure(tmp_path / "wow")
    assert shell.start(False, app.world()) == 0
    local = tmp_path / "Local" / "AHSync"
    assert app.calls == [(local / "data", local / "config.toml", True)] and (local / "data").is_dir()

    mac = FakeApp(tmp_path, MacPlatform(tmp_path / "Users" / "u"))
    mac.configure(tmp_path / "wow")
    assert shell.start(False, mac.world()) == 0
    support = tmp_path / "Users" / "u" / "Library" / "Application Support" / "AHSync"
    assert mac.calls == [(support / "data", support / "config.toml", True)] and (support / "data").is_dir()


def test_DR3_each_start_installs_the_bundled_addon_and_says_what_changed(tmp_path):
    app = FakeApp(tmp_path)
    (tmp_path / "wow" / "Interface" / "AddOns").mkdir(parents=True)
    app.configure(tmp_path / "wow")

    shell.start(False, app.world())
    assert (tmp_path / "wow" / "Interface" / "AddOns" / "AHProfit" / "Core" / "Adapter.lua").read_text() == "-- a\n"
    shell.start(False, app.world())

    lines = [l for l in app.out.getvalue().splitlines() if l.startswith("Addon:")]
    assert lines == ["Addon: AHProfit updated · restart WoW to load it", "Addon: AHProfit installed · up to date"]


def test_FM49_an_unusable_wow_folder_installs_nothing_and_ahsync_still_runs(tmp_path):
    app = FakeApp(tmp_path)
    (tmp_path / "wow").mkdir()
    app.configure(tmp_path / "wow")

    assert shell.start(False, app.world()) == 0

    assert len(app.calls) == 1 and list((tmp_path / "wow").iterdir()) == []
    assert f"Addon: not installed: no Interface/AddOns under {(tmp_path / 'wow').as_posix()}" in app.out.getvalue()


def test_FM48_a_second_launch_hands_over_and_an_autostart_launch_stays_silent(tmp_path):
    app = FakeApp(tmp_path)
    (tmp_path / "wow" / "Interface" / "AddOns").mkdir(parents=True)
    app.configure(tmp_path / "wow")
    data = app.platform.app_dirs().data_dir
    data.mkdir(parents=True)
    held = InstanceLock(data / LOCK_FILE)                 # the running AHSync
    assert held.acquire()
    try:
        assert shell.start(False, app.world()) == 0
        assert sorted(p.name for p in data.iterdir()) == [LOCK_FILE, shell.SHOW_FILE]
        (data / shell.SHOW_FILE).unlink()

        assert shell.start(True, app.world()) == 0
        assert sorted(p.name for p in data.iterdir()) == [LOCK_FILE]
    finally:
        held.release()
    assert app.calls == [] and app.opened == [] and list((tmp_path / "wow" / "Interface" / "AddOns").iterdir()) == []


def test_FM48_the_running_shell_takes_a_show_request(tmp_path):
    def second_launch_seen(data_dir: Path) -> bool:
        if not hasattr(second_launch_seen, "sent"):
            shell.request_show(data_dir)
            second_launch_seen.sent = True
        return not (data_dir / shell.SHOW_FILE).exists()

    app = FakeApp(tmp_path, until=second_launch_seen)
    app.configure(tmp_path / "wow")
    assert shell.start(True, app.world()) == 0
    assert not (app.platform.app_dirs().data_dir / shell.SHOW_FILE).exists() and app.opened == []


def test_a_manual_launch_opens_the_dashboard_once_it_answers_and_an_autostart_launch_never(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: bool(app.opened), port_after=3)
    app.configure(tmp_path / "wow")
    shell.start(False, app.world())
    assert app.opened == ["http://localhost:8765"] and app.probes == 4

    quiet = FakeApp(tmp_path, until=lambda _d: (setattr(quiet, "waits", getattr(quiet, "waits", 0) + 1),
                                                quiet.waits > 200)[1])
    shell.start(True, quiet.world())
    assert quiet.opened == [] and quiet.probes == 0


def test_without_a_config_the_shell_refuses_to_start_until_setup_exists(tmp_path):
    app = FakeApp(tmp_path)
    assert shell.start(False, app.world()) == shell.NO_CONFIG
    assert app.calls == [] and "AHSync can't start: no " in app.out.getvalue()
    assert InstanceLock(app.platform.app_dirs().data_dir / LOCK_FILE).acquire()   # released


def test_ctrl_c_sets_the_stop_event_and_waits_for_run_to_return(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    world = app.world()
    ticks = {"n": 0}

    def interrupt(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] == 3:
            raise KeyboardInterrupt

    world = shell.ShellWorld(**{**world.__dict__, "sleep": interrupt})
    assert shell.start(False, world) == 0
    assert len(app.calls) == 1


def test_a_show_file_that_cannot_be_removed_yet_is_taken_at_a_later_tick(tmp_path, monkeypatch):
    (tmp_path / shell.SHOW_FILE).write_text("x", encoding="utf-8")

    def busy(self, missing_ok=False):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", busy)
    assert shell.take_show_request(tmp_path) is False
    monkeypatch.undo()
    assert shell.take_show_request(tmp_path) is True and shell.take_show_request(tmp_path) is False


def test_an_invalid_config_skips_the_install_and_leaves_the_refusal_to_run(tmp_path):
    app = FakeApp(tmp_path)
    config = app.platform.app_dirs().config_file
    config.parent.mkdir(parents=True)
    config.write_text("dashboard_port = 0\n", encoding="utf-8")
    assert shell.start(False, app.world()) == 0
    assert len(app.calls) == 1 and "Addon:" not in app.out.getvalue()
