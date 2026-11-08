"""Tests for ahsync_app/shell.py: the app's lifecycle around run() (SPEC §3.7.1, §3.7.6, §3.7.7)."""
from __future__ import annotations

import http.client
import io
import json
import socket
import threading
import time
from pathlib import Path

from ahsync.__main__ import LOCK_FILE, InstanceLock, Runtime
from ahsync.config import Config, load_config
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
        self.applied: list[tuple[Path, Path]] = []

    def app_dirs(self) -> AppDirs:
        return AppDirs(self.root / "config.toml", self.root / "data")

    def wow_candidates(self) -> list[Path]:
        return [self.wow]

    def restrict_file(self, path: Path) -> None:
        self.restricted.append(path)

    def run_location_problem(self, exe: Path) -> str | None:
        return None

    def autostart_enable(self, exe: Path, args: tuple[str, ...] = ()) -> None:
        self.autostarts.append(exe)

    def verify_update(self, path: Path, exe: Path) -> str | None:
        return None                                    # Windows' answer: the hash is the check

    def apply_update(self, path: Path, exe: Path) -> None:
        self.applied.append((path, exe))


def status_line(data_dir: Path, topic: str):
    """One Status line from the app's database, or None."""
    db = Database.open(data_dir / "ahsync.db", FakeClock().now_ms(), None)
    try:
        return db.status(topic)
    finally:
        db.close()


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
        self.hooks: list = []
        self.calls_done = False
        self.setup_port = free_port()
        self.probes = 0
        self.out = io.StringIO()
        write(tmp_path / "bundle" / "addon", BUNDLE)

    def run(self, data_dir: Path, config: Path, runtime: Runtime, held_lock=None) -> int:
        """The fake ahsync run()."""
        self.calls.append((data_dir, config, held_lock is not None))
        self.app_infos.append(runtime.app_hooks.app_info)
        self.hooks.append(runtime.app_hooks)
        deadline = time.monotonic() + 5
        while self.until is not None and not self.until(data_dir) and not runtime.should_stop():
            assert time.monotonic() < deadline, "the shell never did what the fake run waits for"
            time.sleep(0.002)
        self.calls_done = True
        return 0

    def port_open(self, port: int) -> bool:
        """The fake dashboard probe."""
        self.probes += 1
        return self.probes > self.port_after

    def world(self) -> shell.ShellWorld:
        """The shell's world over these fakes."""
        return shell.ShellWorld(self.platform, self.run,
                                lambda stop, hooks: Runtime({}, self.tmp, None, FakeClock(), stop, self.out, app_hooks=hooks),
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
    assert not (app.platform.app_dirs().data_dir / shell.SHOW_FILE).exists()
    assert app.opened == ["http://localhost:8765"]          # no window here: the browser shows the dashboard


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


def test_FM51_a_login_entry_that_cannot_be_written_is_a_status_line_the_next_success_clears(tmp_path, caplog):
    app = FakeApp(tmp_path)
    dirs = app.platform.app_dirs()
    dirs.data_dir.mkdir(parents=True)
    session = shell.Session(dirs, InstanceLock(dirs.data_dir / LOCK_FILE), shell.ShellState(False), app.world())
    failing = {"on": True}

    def enable(exe: Path, _args: tuple[str, ...] = ()) -> None:
        if failing["on"]:
            raise PermissionError(13, "Access is denied")
        app.platform.autostarts.append(exe)

    app.platform.autostart_enable = enable
    app.platform.autostart_enabled = lambda: bool(app.platform.autostarts)
    assert session.set_autostart(True) == ("not_set", False)
    line = status_line(dirs.data_dir, "autostart")
    assert (line.level, line.kind, line.data) == ("warn", "autostart_not_set", {"error": "PermissionError"})
    assert "start at login not set: PermissionError" in caplog.text and "Access is denied" not in caplog.text
    failing["on"] = False
    assert session.set_autostart(True) == ("set", True) and status_line(dirs.data_dir, "autostart") is None


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


class FakeWebview:
    """A webview that records what the window asks; its loop runs `script`, then waits for destroy."""

    def __init__(self, script=None, fail_start: Exception | None = None,
                 fail_after_start: Exception | None = None) -> None:
        self.fail_after_start = fail_after_start   # raised once on_started ran (a renderer refused late)
        self.calls: list = []
        self.navigated = None          # set by a test: the user moved on inside the page
        self.script = script
        self.fail_start = fail_start
        self.on_closing = None
        self.destroyed = threading.Event()
        self.starts = 0
        self.error: BaseException | None = None   # a failed check in the script (the window would swallow it)
        self.html = None

    def create(self, url, title, width, height, on_closing, html=None) -> None:
        self.calls.append(("create", url, title, width, height))
        self.on_closing, self.html = on_closing, html

    def start(self, on_started) -> None:
        self.starts += 1
        if self.fail_start is not None:
            raise self.fail_start
        on_started()
        if self.fail_after_start is not None:
            raise self.fail_after_start
        if self.script is not None:
            try:
                self.script(self)
            except BaseException as error:      # end the loop; the test re-raises it
                self.error = error
                return
        if not self.destroyed.wait(5):
            self.error = AssertionError("the window was never destroyed")

    def check(self) -> None:
        """Re-raise what failed inside the loop."""
        if self.error is not None:
            raise self.error

    def show(self) -> None:
        self.calls.append(("show",))

    def hide(self) -> None:
        self.calls.append(("hide",))

    def load(self, url: str) -> None:
        self.calls.append(("load", url))

    def destroy(self) -> None:
        self.calls.append(("destroy",))
        self.destroyed.set()

    def current_url(self):
        loads = [c[1] for c in self.calls if c[0] in ("load", "create")]
        return self.navigated or (loads[-1] if loads else None)


class FakeTray:
    """A tray that keeps the shell's dispatch so a test can click its items."""

    made: list = []

    def __init__(self, state, dispatch, offset_ms) -> None:
        self.state, self.dispatch, self.offset_ms = state, dispatch, offset_ms
        self.events: list[str] = []
        self.stopped = threading.Event()
        FakeTray.made.append(self)

    def start(self) -> None:
        self.events.append("start")

    def run(self) -> None:
        self.events.append("run")
        self.loop_thread = threading.current_thread()
        assert self.stopped.wait(5), "the tray's loop was never stopped"

    def refresh(self) -> None:
        pass

    def stop(self) -> None:
        self.events.append("stop")
        self.stopped.set()


def wait_for(condition, what: str) -> None:
    """Wait up to 5 s for a condition."""
    deadline = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.002)


def windowed(app: FakeApp, view: FakeWebview, make_tray=FakeTray) -> shell.ShellWorld:
    """The fake world with a window and a tray."""
    FakeTray.made.clear()
    return shell.ShellWorld(**{**app.world().__dict__, "webview": lambda _storage: view, "make_tray": make_tray})


def test_DR29_the_window_is_a_view_on_the_dashboard_and_closing_it_never_stops_ahsync(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    home = "http://localhost:8765"

    def script(view: FakeWebview) -> None:
        wait_for(lambda: ("show",) in view.calls, "the manual launch never showed the window")
        assert view.calls[:3] == [("create", None, "AHSync", 1200, 800), ("load", home), ("show",)]   # loading page first
        tray_icon = FakeTray.made[0]
        tray_icon.dispatch("status")
        assert view.calls[-2:] == [("load", f"{home}/#status"), ("show",)]
        assert view.on_closing() is False and view.calls[-1] == ("hide",)
        time.sleep(0.02)
        assert len(app.calls) == 1 and not app.calls_done
        tray_icon.dispatch("open")
        assert view.calls[-2:] == [("load", home), ("show",)]
        tray_icon.dispatch("quit")

    view = FakeWebview(script)
    assert shell.start(False, windowed(app, view)) == 0
    view.check()
    assert view.calls[-1] == ("destroy",) and app.calls_done and app.opened == []
    assert FakeTray.made[0].events == ["start", "stop"]


def test_DR29_an_autostart_launch_keeps_the_window_hidden_until_it_is_asked_for(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")

    def script(view: FakeWebview) -> None:
        time.sleep(0.05)                               # several ticks with the dashboard answering
        assert view.calls == [("create", None, "AHSync", 1200, 800)]                 # the loading page, hidden
        shell.request_show(app.platform.app_dirs().data_dir)   # a second launch (FM48)
        wait_for(lambda: ("show",) in view.calls, "the show request never showed the window")
        FakeTray.made[0].dispatch("quit")

    view = FakeWebview(script)
    assert shell.start(True, windowed(app, view)) == 0
    view.check()
    assert app.opened == []


def test_FM56_without_a_webview_runtime_every_open_goes_to_the_browser_and_nothing_retries(tmp_path, caplog):
    app = FakeApp(tmp_path, until=lambda _d: len(app.opened) >= 3)
    app.configure(tmp_path / "wow")
    data = app.platform.app_dirs().data_dir
    view = FakeWebview(fail_start=RuntimeError("WebView2 runtime missing"))
    world = windowed(app, view)

    def drive() -> None:
        wait_for(lambda: len(app.opened) == 1, "the manual launch never opened the browser")
        FakeTray.made[0].dispatch("open")
        shell.request_show(data)

    driver = threading.Thread(target=drive)
    driver.start()
    assert shell.start(False, world) == 0
    driver.join()
    assert app.opened == ["http://localhost:8765"] * 3 and view.starts == 1
    line = status_line(data, "window")
    assert (line.level, line.kind) == ("warn", "window_unavailable")
    assert caplog.text.count("app window unavailable (RuntimeError)") == 1


def test_FM61_without_a_window_the_tray_s_loop_takes_the_main_thread_until_quit(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    world = windowed(app, FakeWebview(fail_start=RuntimeError("no WebKit")))

    def quit_from_the_tray() -> None:
        wait_for(lambda: FakeTray.made and "run" in FakeTray.made[0].events, "the tray's loop never ran")
        FakeTray.made[0].dispatch("quit")

    driver = threading.Thread(target=quit_from_the_tray)
    driver.start()
    assert shell.start(True, world) == 0
    driver.join()
    tray = FakeTray.made[0]
    assert tray.events == ["run", "stop"] and tray.loop_thread is threading.main_thread()


def test_FM61_a_tray_loop_that_ends_on_its_own_stops_ahsync(tmp_path):
    class EndingTray(FakeTray):
        def run(self) -> None:                     # Ctrl-C swallowed by pystray, or the loop died: no icon, no Quit
            self.events.append("run")

    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    world = windowed(app, FakeWebview(fail_start=RuntimeError("no WebKit")), make_tray=EndingTray)
    assert shell.start(True, world) == 0
    assert EndingTray.made[0].events == ["run", "stop"]


def test_FM61_a_window_refused_after_its_loop_started_keeps_the_one_detached_tray(tmp_path):
    from ahsync_app.window import WindowUnavailable

    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    world = windowed(app, FakeWebview(fail_after_start=WindowUnavailable("renderer mshtml")))
    driver = threading.Thread(target=lambda: (wait_for(lambda: FakeTray.made and "start" in FakeTray.made[0].events,
                                                       "the tray never started"),
                                              FakeTray.made[0].dispatch("quit")))
    driver.start()
    assert shell.start(True, world) == 0
    driver.join()
    assert len(FakeTray.made) == 1 and FakeTray.made[0].events == ["start", "stop"]   # no second loop


def test_FM62_the_session_scrubs_the_key_from_its_own_lines(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    dirs = app.platform.app_dirs()
    world = shell.ShellWorld(**{**app.world().__dict__, "env": {"AUCTIONHOUSE_APIKEY": "SECRETKEY123"}})
    session = shell.Session(dirs, InstanceLock(dirs.data_dir / LOCK_FILE), shell.ShellState(True), world)
    assert session.redactor.scrub("key SECRETKEY123") == "key ***"


def test_FM62_the_shell_s_own_lines_reach_the_log_file(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    data = app.platform.app_dirs().data_dir
    view = FakeWebview(fail_start=RuntimeError("no WebKit"))
    world = windowed(app, view)
    driver = threading.Thread(target=lambda: (wait_for(lambda: FakeTray.made and "run" in FakeTray.made[0].events,
                                                       "the tray's loop never ran"),
                                              FakeTray.made[0].dispatch("quit")))
    driver.start()
    assert shell.start(True, world) == 0
    driver.join()
    text = (data / "ahsync.log").read_text(encoding="utf-8")
    assert "app window unavailable (RuntimeError)" in text and "quit from the tray" in text

def test_a_window_that_starts_clears_an_old_window_unavailable_line(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    data = app.platform.app_dirs().data_dir
    data.mkdir(parents=True)
    shell.record_status(data, app.world(), "window", ("window_unavailable", {}))
    view = FakeWebview(lambda _v: FakeTray.made[0].dispatch("quit"))
    assert shell.start(True, windowed(app, view)) == 0
    view.check()
    assert status_line(data, "window") is None


def test_DR12_the_tray_s_start_at_login_item_toggles_and_the_update_items_wait_for_the_dashboard(tmp_path, caplog):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    app.platform.autostart_enabled = lambda: bool(app.platform.autostarts)
    app.platform.autostart_disable = lambda: app.platform.autostarts.clear()

    def script(_view: FakeWebview) -> None:
        wait_for(lambda: app.calls, "run() never started")    # after the start's self-heal
        tray_icon = FakeTray.made[0]
        assert tray_icon.state().autostart is False and tray_icon.state().configured is True
        tray_icon.dispatch("autostart")
        assert tray_icon.state().autostart is True and app.platform.autostarts == [tmp_path / "AHSync.exe"]
        tray_icon.dispatch("autostart")
        assert tray_icon.state().autostart is False
        tray_icon.dispatch("auto_update")
        tray_icon.dispatch("quit")

    view = FakeWebview(script)
    assert shell.start(True, windowed(app, view)) == 0
    view.check()
    assert "tray item auto_update waits until AHSync runs" in caplog.text       # no dashboard in this fake run


def test_a_tray_that_cannot_start_leaves_ahsync_running(tmp_path, caplog):
    class BrokenTray(FakeTray):
        def start(self) -> None:
            raise OSError("no notification area")

        def run(self) -> None:                     # no window here: the tray would run its own loop (FM61)
            raise OSError("no notification area")

    app = FakeApp(tmp_path, until=lambda _d: bool(app.opened))
    app.configure(tmp_path / "wow")
    world = shell.ShellWorld(**{**app.world().__dict__, "make_tray": BrokenTray})
    assert shell.start(False, world) == 0 and app.opened == ["http://localhost:8765"]
    assert "tray icon unavailable: OSError" in caplog.text


def test_FM51_a_toggle_during_a_refresh_keeps_the_staged_results(tmp_path):
    app = FakeApp(tmp_path)
    dirs = app.platform.app_dirs()
    db = Database.open(dirs.data_dir / "ahsync.db", FakeClock().now_ms(), None)   # run() holds it, mid-refresh
    with db.transaction():
        db.conn.execute("INSERT INTO views_new(page, market, body, built) VALUES ('today', 'm', '{}', 1)")
    session = shell.Session(dirs, InstanceLock(dirs.data_dir / LOCK_FILE), shell.ShellState(False), app.world())
    app.platform.autostart_enabled = lambda: bool(app.platform.autostarts)
    assert session.set_autostart(True) == ("set", True)
    shell.record_status(dirs.data_dir, app.world(), "autostart", ("autostart_not_set", {"error": "OSError"}))
    try:
        assert db.conn.execute("SELECT COUNT(*) FROM views_new").fetchone() == (1,)
        assert db.status("autostart").kind == "autostart_not_set"
    finally:
        db.close()


def test_a_window_that_fails_to_open_is_logged_and_ahsync_runs_on_until_it_returns(tmp_path, caplog):
    def broken(_url: str) -> None:
        raise RuntimeError("the window's form is gone")

    polls_after = []

    def logged_then_more_ticks(_data_dir: Path) -> bool:
        if "window or tray update failed" in caplog.text:
            polls_after.append(1)
        return len(polls_after) > 30                    # dozens of ticks after the failure

    app = FakeApp(tmp_path, until=logged_then_more_ticks, opener=broken)
    app.configure(tmp_path / "wow")
    assert shell.start(False, app.world()) == 0 and app.calls_done
    assert caplog.text.count("window or tray update failed: RuntimeError") == 1   # tried once, not every tick


def test_only_a_build_refreshes_the_login_entry_at_start(tmp_path):
    app = FakeApp(tmp_path)
    app.configure(tmp_path / "wow")
    app.platform.autostart_enabled = lambda: True
    source = shell.ShellWorld(**{**app.world().__dict__, "launch_args": ("ahsync_app/__main__.py",)})
    assert shell.start(False, source) == 0 and app.platform.autostarts == []
    assert shell.start(False, app.world()) == 0 and app.platform.autostarts == [tmp_path / "AHSync.exe"]


class FakeDashboard:
    """The two dashboard calls the tray uses, and the toggles it receives."""

    def __init__(self, state) -> None:
        self.state, self.calls, self.busy = state, [], False

    def tray_facts(self):
        return None if self.busy else (self.state, 1_762_000_000_000)

    def set_auto_update(self, enabled: bool) -> None:
        self.calls.append(("auto_update", enabled))

    def set_update_channel(self, channel: str) -> None:
        self.calls.append(("channel", channel))


def test_DR12_DR13_DR20_the_tray_s_update_items_follow_the_updater_and_toggle_through_the_dashboard(tmp_path, caplog):
    from ahsync.engine.updates import UpdateState

    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    page = "https://github.com/ctng1213/WowAuction/releases/tag/v0.4.0"
    dashboard = FakeDashboard(UpdateState("0.3.1", auto_update=False, tag="v0.4.0", url=page))

    def script(_view: FakeWebview) -> None:
        wait_for(lambda: app.hooks, "run() never started")
        app.hooks[0].dashboard_ready(dashboard)
        tray_icon = FakeTray.made[0]
        state = tray_icon.state()
        assert (state.available, state.downloaded, state.auto_update, state.synced_ms) == ("0.4.0", None, False,
                                                                                          1_762_000_000_000)
        tray_icon.dispatch("download")
        tray_icon.dispatch("auto_update")
        time.sleep(0.05)                                            # each toggle runs on its own thread
        tray_icon.dispatch("beta")
        time.sleep(0.05)
        dashboard.state = UpdateState("0.3.1", channel="beta", tag="v0.4.0", url=page, downloaded="0.4.0")
        assert (tray_icon.state().available, tray_icon.state().downloaded) == (None, "0.4.0")
        dashboard.busy = True                                       # a re-plan holds the dashboard: the last state
        assert tray_icon.state().downloaded == "0.4.0" and tray_icon.state().channel == "beta"
        tray_icon.dispatch("restart_update")
        tray_icon.dispatch("beta")
        tray_icon.dispatch("quit")

    view = FakeWebview(script)
    with caplog.at_level("INFO", logger="ahsync.app"):
        assert shell.start(True, windowed(app, view)) == 0
    view.check()
    assert app.opened == [page]                                     # the release page, in the browser
    assert dashboard.calls == [("auto_update", True), ("channel", "beta"), ("channel", "stable")]
    assert "restart to update" in caplog.text                       # the stop the install follows (M27)
    assert app.hooks[0].verify_update(tmp_path / "x.exe") is None     # the platform's check (Windows: the hash only)


class Installing(FolderPlatform):
    """Records each install with whether another process could take the lock at that moment."""

    def update_asset_name(self, version: str) -> str:
        return f"AHSync-{version}-windows-x64-setup.exe"

    def apply_update(self, path: Path, exe: Path) -> None:
        from ahsync.__main__ import LOCK_FILE, InstanceLock
        lock = InstanceLock(self.app_dirs().data_dir / LOCK_FILE)
        free = lock.acquire()
        if free:
            lock.release()
        self.applied.append((path.name, exe.name, free))


def offer_update(data: Path) -> Path:
    """Leave DR20's verified 0.4.0 installer waiting, as the download half does (the shell runs 0.0.0 + this patch)."""
    from ahsync.app.update_check import write_meta
    setup = data / "updates" / "AHSync-0.4.0-windows-x64-setup.exe"
    setup.parent.mkdir(parents=True, exist_ok=True)
    setup.write_bytes(b"installer")
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    write_meta(db, {"tag": "v0.4.0", "checked_ms": 5, "downloaded": "0.4.0", "downloaded_path": str(setup)})
    db.close()
    return setup


def pending(data: Path) -> str | None:
    """update.pending in the app's database."""
    from ahsync.app.update_check import read_meta
    db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
    try:
        return read_meta(db).get("pending")
    finally:
        db.close()


def test_DR22_a_waiting_update_installs_at_the_next_start_before_ahsync_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.version, "VERSION", "0.3.1")
    app = FakeApp(tmp_path, Installing(tmp_path))
    app.configure(tmp_path / "wow")
    data = app.platform.app_dirs().data_dir
    setup = offer_update(data)
    assert shell.start(True, app.world()) == 0
    assert app.calls == [] and app.platform.applied == [(setup.name, "AHSync.exe", False)]   # run() never started;
    assert pending(data) == "0.4.0"                       # the lock is still ours: the installer waits for it


def test_FM78_a_plain_or_stop_file_quit_never_installs(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.version, "VERSION", "0.3.1")
    app = FakeApp(tmp_path, Installing(tmp_path), until=lambda data: offer_update(data) is not None)
    app.configure(tmp_path / "wow")
    assert shell.start(True, app.world()) == 0            # downloaded during the run, then run() returned 0
    assert app.calls_done and app.platform.applied == []  # the uninstaller's or an installer's stop: no install
    assert pending(app.platform.app_dirs().data_dir) is None


def test_DR22_restart_to_update_installs_once_run_returns_and_the_lock_is_free(tmp_path, monkeypatch):
    monkeypatch.setattr(shell.version, "VERSION", "0.3.1")
    answers: list[bool] = []

    def restart_after_download(data: Path) -> bool:
        """The update arrives during the run; then the Status card's button asks for the restart."""
        if not answers:
            offer_update(data)
            db = Database.open(data / "ahsync.db", FakeClock().now_ms(), None)
            try:
                answers.append(app.hooks[0].restart(db, Config()))
            finally:
                db.close()
        return False                                      # run() returns on the stop the restart set

    app = FakeApp(tmp_path, Installing(tmp_path), until=restart_after_download)
    app.configure(tmp_path / "wow")
    assert shell.start(True, app.world()) == 0
    assert answers == [True] and app.platform.applied == [("AHSync-0.4.0-windows-x64-setup.exe", "AHSync.exe", True)]


def test_DR32_the_window_opens_on_the_loading_page_and_loads_the_dashboard_once_it_answers(tmp_path):
    app = FakeApp(tmp_path, until=lambda _d: False)
    app.configure(tmp_path / "wow")
    app.port_after = 10**9                              # the dashboard does not answer yet
    home = "http://localhost:8765"

    def script(view: FakeWebview) -> None:
        wait_for(lambda: ("show",) in view.calls, "the manual launch never showed the loading page")
        assert view.calls[0] == ("create", None, "AHSync", 1200, 800)       # the bundled page, never the URL
        assert view.html is not None and "Starting AHSync" in view.html
        FakeTray.made[0].dispatch("open")                                    # Open dashboard while starting
        time.sleep(0.02)
        assert not [c for c in view.calls if c[0] == "load"]                 # nothing loads before it answers
        app.port_after = app.probes                                          # now it answers
        wait_for(lambda: ("load", home) in view.calls, "the dashboard never loaded once it answered")
        time.sleep(0.02)
        assert [c for c in view.calls if c[0] == "load"] == [("load", home)]   # and only once
        FakeTray.made[0].dispatch("quit")

    view = FakeWebview(script)
    assert shell.start(False, windowed(app, view)) == 0
    view.check()
