"""Tests for ahsync_app/shell.py: the app's lifecycle around run() (SPEC §3.7.1, §3.7.6, §3.7.7)."""
from __future__ import annotations

import http.client
import io
import json
import socket
import time
from pathlib import Path

from ahsync.__main__ import LOCK_FILE, InstanceLock, Runtime
from ahsync.config import load_config
from ahsync.db.sqlite import Database
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
    """A platform whose per-user folder is tmp/Local/AHSync on any OS (the real Windows paths: test_windows.py),
    whose WoW candidate is tmp/World of Warcraft/_classic_beta_, and which records its writers."""

    def __init__(self, root: Path) -> None:
        self.root = root / "Local" / "AHSync"
        self.wow = root / "World of Warcraft" / "_classic_beta_"
        self.restricted: list[Path] = []
        self.autostarts: list[Path] = []

    def app_dirs(self) -> AppDirs:
        return AppDirs(self.root / "config.toml", self.root / "data")

    def wow_candidates(self) -> list[Path]:
        return [self.wow]

    def restrict_file(self, path: Path) -> None:
        self.restricted.append(path)

    def run_location_problem(self, exe: Path) -> str | None:
        return None

    def autostart_enable(self, exe: Path) -> None:
        self.autostarts.append(exe)


def free_port() -> int:
    """A loopback port that was free a moment ago."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeApp:
    """A fake run(), browser and dashboard port. run() records its arguments, then waits for the stop event,
    for `until()` to hold, or returns at once."""

    def __init__(self, tmp_path: Path, platform=None, until=None, port_after: int = 0, opener=None) -> None:
        self.tmp = tmp_path
        self.platform = platform or FolderPlatform(tmp_path)
        self.until = until
        self.port_after = port_after          # the dashboard answers from this probe on
        self.calls: list[tuple[Path, Path, bool]] = []
        self.opened: list[str] = []
        self.opener = opener or self.opened.append
        self.app_infos: list = []
        self.setup_port = free_port()
        self.probes = 0
        self.out = io.StringIO()
        write(tmp_path / "bundle" / "addon", BUNDLE)

    def run(self, data_dir: Path, config: Path, runtime: Runtime, held_lock=None) -> int:
        """The fake ahsync run()."""
        self.calls.append((data_dir, config, held_lock is not None))
        self.app_infos.append(runtime.app_info)
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
                                lambda stop, info: Runtime({}, self.tmp, None, FakeClock(), stop, self.out, app_info=info),
                                self.opener, self.port_open, lambda _s: time.sleep(0.001),
                                self.tmp / "bundle" / "addon", {}, self.tmp / "home", self.out, FakeClock(),
                                self.tmp / "AHSync.exe", self.setup_port)

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


def request(port: int, method: str, path: str, body: dict | None = None, token: str = "") -> tuple[int, dict, bytes]:
    """One request to the shell's server, as the setup page sends it."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}", "X-AHSync-Token": token,
               "Content-Type": "application/json"}
    conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
    reply = conn.getresponse()
    raw, location = reply.read(), reply.getheader("Location")
    conn.close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {"location": location}
    return reply.status, data, raw


def page_token(port: int) -> str:
    """The token embedded in the served setup page."""
    _status, _data, raw = request(port, "GET", "/setup")
    return raw.decode().split('name="ahsync-token" content="')[1].split('"')[0]


def make_client(root: Path) -> Path:
    """A WoW client at root with Interface/AddOns and two accounts."""
    (root / "Interface" / "AddOns").mkdir(parents=True)
    for name in ("A1", "A2"):
        (root / "WTF" / "Account" / name).mkdir(parents=True)
    return root


def test_DR5_without_a_config_the_shell_serves_setup_and_run_waits(tmp_path):
    seen = {}

    def browse(url: str) -> None:
        port = app.setup_port
        seen["url"] = url
        seen["root"] = request(port, "GET", "/")[0:2]
        token = page_token(port)
        seen["app"] = request(port, "GET", "/api/app")[1]
        seen["view"] = request(port, "GET", "/api/views/status")[0:2]
        seen["run_started"] = bool(app.calls)
        seen["save"] = request(port, "POST", "/api/setup", {"wow_path": seen["app"]["wow_path"], "ruleset": "pvp",
                                                            "faction": "horde", "api_key": "", "autostart": False},
                               token)[0:2]

    app = FakeApp(tmp_path, opener=browse)
    make_client(app.platform.wow)
    assert shell.start(False, app.world()) == 0

    assert seen["url"] == f"http://localhost:{app.setup_port}/setup"
    assert seen["root"] == (302, {"location": "/setup"})
    assert seen["app"]["setup"] is True and seen["app"]["wow_path"] == str(app.platform.wow)
    assert seen["app"]["wow_check"] == {"ok": True, "pill": "found", "text": "found Interface/AddOns · 2 accounts"}
    assert seen["view"] == (404, {"error": "no_refresh_yet"}) and seen["run_started"] is False
    assert seen["save"] == (200, {"set": True, "restart_wow": True})
    assert len(app.calls) == 1                                         # the hand-over: run() after the save


def test_DR5_an_existing_config_with_a_bad_folder_is_not_setup_mode(tmp_path):
    app = FakeApp(tmp_path)
    (tmp_path / "Games" / "WoW").mkdir(parents=True)
    app.configure(tmp_path / "Games" / "WoW")
    config_file = app.platform.app_dirs().config_file
    before = config_file.read_bytes()

    assert shell.start(False, app.world()) == 0

    info = app.app_infos[0]()
    assert len(app.calls) == 1 and info["setup"] is False and config_file.read_bytes() == before
    assert info["wow_check"] == {"ok": False, "pill": "not found",
                                 "text": f"no Interface/AddOns under {(tmp_path / 'Games' / 'WoW').as_posix()}"}
    assert info["addon"]["class"] == "amber" and info["addon"]["text"].startswith("not installed: no Interface/AddOns")


def test_DR7_one_save_writes_everything_installs_the_addon_and_hands_over(tmp_path):
    seen = {}

    def browse(_url: str) -> None:
        port = app.setup_port
        seen["save"] = request(port, "POST", "/api/setup", {"wow_path": str(app.platform.wow), "ruleset": "pvp",
                                                            "faction": "horde", "api_key": " abc ", "autostart": True},
                               page_token(port))

    app = FakeApp(tmp_path, opener=browse)
    make_client(app.platform.wow)
    assert shell.start(False, app.world()) == 0

    status, answer, raw = seen["save"]
    assert (status, answer) == (200, {"set": True, "restart_wow": True}) and b"abc" not in raw
    dirs = app.platform.app_dirs()
    config = load_config(dirs.config_file, {}, tmp_path / "home")
    assert list(config.markets) == ["forever.pvp.horde.us"] and config.wow_path == str(app.platform.wow)
    key_file = tmp_path / "home" / ".ahsync" / "api_key"
    assert config.api_key == "abc" and app.platform.restricted == [key_file]
    assert app.platform.autostarts == [tmp_path / "AHSync.exe"]
    assert (app.platform.wow / "Interface" / "AddOns" / "AHProfit" / "Core" / "Adapter.lua").exists()
    assert app.calls == [(dirs.data_dir, dirs.config_file, True)]
    assert app.out.getvalue().count("Addon: AHProfit updated · restart WoW to load it") == 1
    assert app.opened == [] and "abc" not in app.out.getvalue()
    info = app.app_infos[0]()
    assert info["setup"] is False and info["api_key_set"] is True and "abc" not in json.dumps(info)


def test_FM50_the_key_line_goes_to_status_and_a_later_save_clears_it(tmp_path):
    app = FakeApp(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    shell.record_key_status(data, app.world(), None)                 # nothing to clear: no database is made
    assert not (data / "ahsync.db").exists()

    shell.record_key_status(data, app.world(), "permission denied")
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    try:
        lines = [(m.topic, m.level, m.kind, m.data) for m in db.statuses()]
        assert ("api_key_setup", "warn", "api_key_not_saved", {"reason": "permission denied"}) in lines
    finally:
        db.close()
    shell.record_key_status(data, app.world(), None)
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    try:
        assert all(m.topic != "api_key_setup" for m in db.statuses())
    finally:
        db.close()


def test_a_setup_port_in_use_is_reported_and_an_autostart_setup_opens_nothing(tmp_path):
    app = FakeApp(tmp_path)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", app.setup_port))
        busy.listen()
        assert shell.start(False, app.world()) == shell.NO_SETUP
    assert f"Setup not started: port {app.setup_port} is not available" in app.out.getvalue() and app.calls == []

    quiet = FakeApp(tmp_path)
    world = quiet.world()

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    assert shell.start(True, shell.ShellWorld(**{**world.__dict__, "sleep": interrupt})) == 0
    assert quiet.opened == [] and quiet.calls == []


def test_start_at_login_failures_after_setup_are_logged_not_fatal(tmp_path, caplog):
    app = FakeApp(tmp_path)

    def refuse(exe: Path) -> None:
        raise PermissionError("registry")

    app.platform.autostart_enable = refuse
    shell.enable_autostart(app.world())
    app.platform.autostart_enable = Platform.autostart_enable.__get__(app.platform)   # not built until M24
    shell.enable_autostart(app.world())
    assert "start at login not set: PermissionError" in caplog.text


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


def test_setup_logs_to_the_data_folder_without_the_key_and_the_stop_file_ends_it(tmp_path):
    def browse(_url: str) -> None:
        port = app.setup_port
        request(port, "POST", "/api/setup", {"wow_path": str(app.platform.wow), "ruleset": "pvp", "faction": "horde",
                                             "api_key": "abc", "autostart": False}, page_token(port))

    app = FakeApp(tmp_path, opener=browse)
    make_client(app.platform.wow)
    assert shell.start(False, app.world()) == 0
    log_text = (app.platform.app_dirs().data_dir / "ahsync.log").read_text(encoding="utf-8")
    assert "setup saved; handing over" in log_text and "key file written" in log_text and "abc" not in log_text
    assert f"Setup: http://localhost:{app.setup_port}/setup" in app.out.getvalue()

    stopped = FakeApp(tmp_path / "second")
    data = stopped.platform.app_dirs().data_dir

    def stop_soon(_seconds: float) -> None:
        (data / "ahsync.stop").write_text("stop", encoding="utf-8")

    world = stopped.world()
    assert shell.start(True, shell.ShellWorld(**{**world.__dict__, "sleep": stop_soon})) == 0
    assert stopped.calls == [] and not (data / "ahsync.stop").exists()
