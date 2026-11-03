"""The app's lifecycle around `ahsync.run()` (SPEC §3.7.1, §3.7.5, §3.7.6, §3.7.7).

At each start, in order: take the instance lock (a second launch hands over to the running one through
`<data>/ahsync.show` and exits); create the window hidden and give the main thread to its GUI loop, which
starts the tray and a worker. The worker serves the setup page when `config.toml` is missing (the save installs
the addon), otherwise installs the bundled addon; refreshes start at login; runs AHSync; and ticks once a second
until it returns: a show request or the first answer of a manual launch shows the window, and the tray's menu
follows the state. Quit (the tray), Ctrl-C or the stop file end the run; the window is destroyed, which ends
the loop, and the lock is released only after `run()` has returned. Without a window (FM56) the browser opens
every page and the main thread waits for the worker.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import IO, Protocol

from ahsync import version
from ahsync.__main__ import (API_KEY_TOPIC, LOCK_FILE, STOP_FILE, InstanceLock, Runtime, remove_stop_file,
                             setup_logging)
from ahsync.collect.client import Redactor
from ahsync.app.setup import SetupRequest, save_setup
from ahsync.app.wow_paths import detect
from ahsync.clock import Clock
from ahsync.config import Config, ConfigError, load_config, resolve_api_key
from ahsync.db.sqlite import Database, DatabaseError
from ahsync.export.addon_install import AddonResult, install_addon
from ahsync.web.server import AppHooks, new_token, open_server_db, start_dashboard, stop_dashboard
from ahsync.web.setup_app import SetupApp
from ahsync_app import autostart, tray
from ahsync_app.app_info import app_payload
from ahsync_app.platform.base import AppDirs, Platform
from ahsync.engine.updates import UpdateState, newer, ready, tag_version
from ahsync_app.tray import TrayState
from ahsync_app.window import Webview, Window, WindowUnavailable

log = logging.getLogger("ahsync.app")

SHOW_FILE = "ahsync.show"     # in data/: a second launch asks the running shell to show its window (FM48)
TICK_S = 1.0
NO_SETUP = 2                  # exit code when the setup page can't be served (its port is taken)
AUTOSTART_TOPIC = "autostart"  # the Status topic of autostart_not_set (FM51)
WINDOW_TOPIC = "window"        # the Status topic of window_unavailable (FM56)


class Tray(Protocol):
    """The tray icon as the shell drives it (tray.PystrayIcon; a fake in tests)."""

    def start(self) -> None:
        """Show the icon."""

    def refresh(self) -> None:
        """Rebuild the menu when the state changed."""

    def stop(self) -> None:
        """Remove the icon; ends run()'s loop."""

    def run(self) -> None:
        """Show the icon and run its loop on this (the main) thread until stop() (FM61): when there is no window.

        A stop() before the loop is ready ends it once it is; a stop() before run() means it never starts.
        """


@dataclass
class ShellState:
    """What the running shell knows about itself; the window, the tray and `GET /api/app` read it (M23, M24)."""

    autostart: bool                    # launched by the login entry: no window shown, no browser
    addon: AddonResult | None = None   # this start's addon install (setup's, after a setup); None: none yet
    opened: bool = False               # the dashboard has been shown for this launch
    shows: int = 0                     # show requests handled (second launches)


@dataclass(frozen=True)
class ShellWorld:
    """What the shell gets from the outside world, so tests replace all of it."""

    platform: Platform
    run: Callable[..., int]                                  # ahsync.__main__.run
    make_runtime: Callable[[Callable[[], bool], AppHooks], Runtime]   # (should_stop, app hooks) -> Runtime
    opener: Callable[[str], object]                          # the system browser (the window's fallback, FM56)
    port_open: Callable[[int], bool]                         # whether the dashboard answers on localhost:<port>
    sleep: Callable[[float], None]
    bundle_addon_dir: Path                                   # the addon/ folder shipped with the app
    env: dict
    home: Path
    out: IO[str]
    clock: Clock                                             # Status lines (FM50, FM51, FM56), the hover time
    exe: Path                                                # what the login entry starts (start at login)
    setup_port: int = Config().dashboard_port                # setup's port: config.toml's default (no config yet)
    launch_args: tuple[str, ...] = ()                        # exe's arguments before --autostart (source: the script)
    webview: Callable[[Path], Webview] | None = None         # storage folder -> the webview; None: no window
    make_tray: Callable[..., Tray] | None = None             # (state, dispatch, offset_ms) -> the tray; None: none


def request_show(data_dir: Path) -> None:
    """Ask the running shell to show its window: write the show file (FM48).

    Args:
        data_dir: The app's data folder.
    """
    (data_dir / SHOW_FILE).write_text("show requested by a second launch\n", encoding="utf-8")


def take_show_request(data_dir: Path) -> bool:
    """Consume a pending show request (FM48).

    Args:
        data_dir: The app's data folder.

    Returns:
        True when a show file was there (it is removed); False otherwise or when it can't be removed yet.
    """
    try:
        (data_dir / SHOW_FILE).unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False                   # still being written on Windows: the next tick takes it
    return True


def load_settings(config_file: Path, world: ShellWorld) -> Config | None:
    """The settings the shell needs before `run()`: the WoW folder and the dashboard port.

    Args:
        config_file: config.toml.
        world: For the environment and home folder (the key lookup, SPEC §3.6).

    Returns:
        The config; None when it is invalid, which `run()` then reports.
    """
    try:
        return load_config(config_file, world.env, world.home)
    except ConfigError:
        return None


def start(autostart_launch: bool, world: ShellWorld) -> int:
    """Run the app from launch to quit (SPEC §3.7.1; DR1, DR3, DR5, DR29, FM48, FM49, FM56).

    Args:
        autostart_launch: Launched with --autostart (by the login entry): the window stays hidden.
        world: The outside world.

    Returns:
        0 after a hand-over to the running app, a normal stop or a setup left early; NO_SETUP when the setup
        page can't be served; else run()'s exit code.

    Side effects:
        Creates the per-user folders; installs the addon; writes the show file when another
        instance holds the lock (not on an --autostart launch).
    """
    dirs = world.platform.app_dirs()
    dirs.data_dir.mkdir(parents=True, exist_ok=True)
    dirs.config_file.parent.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(dirs.data_dir / LOCK_FILE)
    if not lock.acquire():
        if not autostart_launch:       # the login entry starts at once when turned on: stay silent (FM48)
            request_show(dirs.data_dir)
        return 0
    try:
        return Session(dirs, lock, ShellState(autostart_launch), world).run()
    finally:
        lock.release()


class Session:
    """One start of the app while it holds the lock: the window's loop, the tray and the worker (SPEC §3.7.1)."""

    def __init__(self, dirs: AppDirs, lock: InstanceLock, state: ShellState, world: ShellWorld) -> None:
        """Prepare the window and the stop event; nothing runs yet.

        Args:
            dirs: The per-user folders.
            lock: The instance lock this shell holds; run() uses it and leaves its release to start().
            state: The shell's state.
            world: The outside world.
        """
        self.dirs, self.lock, self.state, self.world = dirs, lock, state, world
        self.stop = threading.Event()
        self.window = Window(world.webview(dirs.data_dir / "webview") if world.webview else None, world.opener)
        self.tray: Tray | None = None
        self.worker: threading.Thread | None = None
        self.setup = not dirs.config_file.exists()      # only a missing config: an existing one is never rewritten (DR5)
        self.config = None if self.setup else load_settings(dirs.config_file, world)
        self.redactor = Redactor()         # the shell's log scrubs the key outside setup and run() too (FM62)
        self.redactor.add(self.config.api_key if self.config else None)
        self.port = world.setup_port if self.setup else (self.config or Config()).dashboard_port
        self.code = 0
        self._begun = threading.Lock()
        self.dashboard = None              # run()'s DashboardApp, once it serves (dashboard_ready)
        self.toggles: list[threading.Thread] = []   # the tray's toggle threads (off_the_tray)
        self.update_facts: tuple[UpdateState, int | None] = (UpdateState(version.VERSION), None)

    def home(self) -> str:
        """The dashboard's URL, e.g. "http://localhost:8765".

        Returns:
            The URL on this run's port.
        """
        return f"http://localhost:{self.port}"

    def page(self) -> str:
        """What Open dashboard shows: the setup page while setup runs, else the dashboard.

        Returns:
            The URL.
        """
        return f"{self.home()}/setup" if self.setup else self.home()

    def run(self) -> int:
        """Give the main thread to the window's loop until Quit; without a window, to the tray's (FM61).

        The shell's own lines go to data/ahsync.log for the whole run (FM62).

        Returns:
            The worker's exit code.
        """
        release_log = setup_logging(self.dirs.data_dir, self.redactor)
        try:
            if not self.run_window():
                if self.world.webview is not None:
                    record_status(self.dirs.data_dir, self.world, WINDOW_TOPIC, ("window_unavailable", {}))   # FM56
                if self.begin(tray_loop=True):     # not when the window's loop already started the tray (detached)
                    self.run_tray()
            self.wait()
            return self.code
        finally:
            release_log()

    def run_window(self) -> bool:
        """Run the window's loop on the main thread until Quit destroys the window.

        Returns:
            True when the loop ran (or Ctrl-C ended it); False when there is no window (FM56).
        """
        if not self.window.create(self.page()):
            return False
        try:
            self.window.start(self.begin)  # blocks until the worker's end destroys the window
        except WindowUnavailable:
            return False
        except KeyboardInterrupt:
            pass
        self.stop.set()                    # the loop ended (Quit, or the OS ending the session): stop AHSync
        return True

    def run_tray(self) -> None:
        """Run the tray's loop on the main thread until the worker's end stops it (FM61): macOS draws only there.

        However the loop ends, AHSync stops: without the icon there would be no Quit (pystray swallows Ctrl-C on
        Windows and ends its loop; on macOS its SIGINT handler terminates the process). A tray whose loop can't run
        at all is logged and AHSync runs on.
        """
        if self.tray is None:
            return
        try:
            self.tray.run()
        except KeyboardInterrupt:
            pass
        except Exception as error:         # no tray (a missing backend): AHSync still runs
            log.warning("tray icon unavailable: %s", type(error).__name__)
            return
        self.stop.set()

    def begin(self, tray_loop: bool = False) -> bool:
        """Start the tray and the worker, once (the loop's start callback, or the fallback).

        Args:
            tray_loop: The tray will run its own loop (run_tray); else it joins the window's now.

        Returns:
            True when this call started them; False when an earlier one had.
        """
        with self._begun:
            if self.worker is not None:
                return False
            if self.window.state.created:
                record_status(self.dirs.data_dir, self.world, WINDOW_TOPIC, None)
            if self.world.make_tray is not None:
                self.tray = self.world.make_tray(self.tray_state, self.act, self.offset_ms)
                try:
                    if not tray_loop:
                        self.tray.start()
                except Exception as error:     # no tray (a missing backend): AHSync still runs
                    log.warning("tray icon unavailable: %s", type(error).__name__)
                    self.tray = None
            self.worker = threading.Thread(target=self.lifecycle, name="ahsync-app", daemon=True)
            self.worker.start()
            return True

    def wait(self) -> None:
        """Wait for the worker on the main thread; Ctrl-C asks it to stop (run() returns at its next tick)."""
        while self.worker is not None and self.worker.is_alive():
            try:
                self.worker.join(0.5)
            except KeyboardInterrupt:
                self.stop.set()

    def lifecycle(self) -> None:
        """The worker: setup when needed, then AHSync; at the end the tray goes and the window is destroyed."""
        try:
            self.code = self.steps()
        except Exception:
            log.exception("the app stopped on an error")
            self.code = 1
        finally:
            if self.tray is not None:
                self.tray.stop()
            self.window.quit()

    def steps(self) -> int:
        """Setup when needed, the addon install, start at login's self-heal, then run() (SPEC §3.7.1 order).

        Returns:
            The exit code start() returns.
        """
        if self.setup:
            saved = serve_setup(self)
            if saved is not True:
                return saved
            self.setup = False
            self.config = load_settings(self.dirs.config_file, self.world)
            self.redactor.add(self.config.api_key if self.config else None)
            self.port = (self.config or Config()).dashboard_port
        config = self.config
        if config is not None and self.state.addon is None:   # only the lock owner installs (SPEC §3.7.6)
            self.state.addon = install_addon(self.world.bundle_addon_dir, config.wow_path)
        if self.state.addon is not None:
            print(f"Addon: {self.state.addon.line['text']}", file=self.world.out)
        if not self.world.launch_args:     # a build only: a source run never takes over the installed app's entry
            autostart.self_heal(self.world.platform, self.world.exe)
        info = partial(app_payload, self.world, self.state, False, config.wow_path if config else "",
                       bool(config and config.api_key))
        return run_until_stopped(self, AppHooks(info, self.set_autostart, self.world.platform.update_asset_name,
                                                self.verify_update, self.dashboard_ready))

    def verify_update(self, path: Path) -> str | None:
        """The platform's signature check of a download; until M27 builds it, nothing beyond the hash (SPEC §3.7.9).

        Args:
            path: The downloaded file, its hash checked.

        Returns:
            None when it may be installed; else the reason.
        """
        try:
            return self.world.platform.verify_update(path)
        except NotImplementedError:
            return None

    def dashboard_ready(self, app: object) -> None:
        """Keep run()'s dashboard: the tray reads the update state through it and toggles through its handlers.

        Args:
            app: The DashboardApp.
        """
        self.dashboard = app

    def set_autostart(self, enabled: bool) -> tuple[str, bool]:
        """Turn start at login on or off for any control, keeping the Status line (SPEC §3.7.5; SEC14, FM51).

        Args:
            enabled: The wanted state.

        Returns:
            (autostart.SET, BAD_LOCATION or NOT_SET; the real state afterwards).
        """
        outcome, now_enabled, error = autostart.request(self.world.platform, self.world.exe, enabled,
                                                        self.world.launch_args)
        if outcome == autostart.NOT_SET:
            record_status(self.dirs.data_dir, self.world, AUTOSTART_TOPIC, ("autostart_not_set", {"error": error}))
        elif outcome == autostart.SET:
            record_status(self.dirs.data_dir, self.world, AUTOSTART_TOPIC, None)
        return outcome, now_enabled

    def tray_state(self) -> TrayState:
        """The tray menu's state now (DR12): the update items from the dashboard's state, kept while it is busy.

        Returns:
            The state.
        """
        facts = self.dashboard.tray_facts() if self.dashboard is not None else None
        if facts is not None:
            self.update_facts = facts
        update, synced = self.update_facts
        downloaded = update.downloaded if ready(update) and not update.cant_replace else None
        available = tag_version(update.tag) if newer(update.tag, update.version) and downloaded is None else None
        return TrayState(configured=not self.setup, version=version.VERSION,
                         autostart=autostart.is_enabled(self.world.platform), auto_update=update.auto_update,
                         channel=update.channel, available=available, downloaded=downloaded, synced_ms=synced)

    def offset_ms(self) -> int:
        """Local time minus UTC now, for the hover text.

        Returns:
            Milliseconds.
        """
        return self.world.clock.local_offset_ms(self.world.clock.now_ms())

    def act(self, action: str) -> None:
        """Do what a tray item asks (DR12); called on the tray's thread.

        Args:
            action: One of the tray module's action names.
        """
        if action == tray.OPEN:
            self.window.open(self.page())
        elif action == tray.STATUS:
            self.window.open(f"{self.home()}/#status")
        elif action == tray.AUTOSTART:
            self.set_autostart(not autostart.is_enabled(self.world.platform))
        elif action == tray.QUIT:
            log.info("quit from the tray")
            self.stop.set()            # run() returns at its next tick; the worker then destroys the window
        else:
            self.act_on_update(action)
        if self.tray is not None:
            self.tray.refresh()

    def act_on_update(self, action: str) -> None:
        """A tray update item: the toggles go through the dashboard's handlers, as the Status card's do (SEC15).

        Args:
            action: tray.AUTO_UPDATE, BETA, DOWNLOAD or RESTART_UPDATE.
        """
        update, _synced = self.update_facts
        if action == tray.DOWNLOAD and update.url:
            self.world.opener(update.url)        # the release page, in the browser (SPEC §3.7.1)
        elif action == tray.RESTART_UPDATE:
            log.info("Restart to update installs from M27")
        elif self.dashboard is None:
            log.info("tray item %s waits until AHSync runs", action)
        elif action == tray.AUTO_UPDATE:
            self.off_the_tray(self.dashboard.set_auto_update, not update.auto_update)
        elif action == tray.BETA:
            self.off_the_tray(self.dashboard.set_update_channel, "stable" if update.channel == "beta" else "beta")

    def off_the_tray(self, toggle: Callable[[object], object], value: object) -> threading.Thread:
        """Run a toggle on its own thread: it waits for the dashboard's lock, and a re-plan must not freeze the menu.

        Args:
            toggle: The dashboard's set_auto_update or set_update_channel.
            value: Its argument.

        Returns:
            The started thread (tests join it).
        """
        thread = threading.Thread(target=toggle, args=(value,), name="ahsync-tray-toggle", daemon=True)
        thread.start()
        self.toggles.append(thread)
        return thread

    def tick(self) -> None:
        """One pass of the worker's loop: a show request, the first show of a manual launch, the tray's state.

        A window or tray failure is logged and never ends AHSync.
        """
        try:
            if take_show_request(self.dirs.data_dir):
                self.state.shows += 1
                self.window.open(self.page())
            if (not self.setup and not self.state.autostart and not self.state.opened
                    and self.world.port_open(self.port)):
                self.state.opened = True
                self.window.open(self.home())
            if self.tray is not None:
                self.tray.refresh()
        except Exception as error:
            log.warning("window or tray update failed: %s", type(error).__name__)


def serve_setup(session: Session) -> bool | int:
    """Serve the setup page until it is saved, then stop it for the normal run (SPEC §3.7.4; DR5, DR7, FM50).

    Args:
        session: The running session (folders, port, state, window, stop event).

    Returns:
        True after a save; 0 when Quit, Ctrl-C or the stop file ended it; NO_SETUP when the port can't be bound.
    """
    world, dirs, state = session.world, session.dirs, session.state
    detected = detect(world.platform.wow_candidates())
    wow = str(detected) if detected is not None else ""

    def after_save(request: SetupRequest) -> bool:
        """What follows a saved setup: start at login, the addon install, a settled key line.

        Args:
            request: The checked setup.

        Returns:
            Whether WoW must restart to load the addon.
        """
        if request.autostart:
            session.set_autostart(True)
        state.addon = install_addon(world.bundle_addon_dir, request.wow_path)
        record_key_status(dirs.data_dir, world, None)
        return state.addon.restart_wow

    key_set = resolve_api_key(world.env, world.home, "") != ""
    app = SetupApp(new_token(), session.port, partial(app_payload, world, state, True, wow, key_set),
                   partial(save_setup, config_file=dirs.config_file, home=world.home,
                           restrict_file=world.platform.restrict_file),
                   after_save, partial(record_key_status, dirs.data_dir, world))
    app.redact = session.redactor.add                    # setup's keys stay scrubbed after setup ends (FM62)
    release_log = setup_logging(dirs.data_dir, session.redactor)
    try:
        return wait_for_setup(app, session)
    finally:
        release_log()


def wait_for_setup(app: SetupApp, session: Session) -> bool | int:
    """Serve setup until it is saved, Quit, Ctrl-C or the stop file (SPEC §3.7.4).

    Args:
        app: The setup requests.
        session: The running session.

    Returns:
        True after a save; 0 after Quit, Ctrl-C or the stop file; NO_SETUP when the port can't be bound.
    """
    world, data_dir, state = session.world, session.dirs.data_dir, session.state
    try:
        server = start_dashboard(app, session.port)
    except OSError:
        print(f"Setup not started: port {session.port} is not available", file=world.out)
        return NO_SETUP
    url = session.page()
    print(f"Setup: {url}", file=world.out)
    remove_stop_file(data_dir / STOP_FILE)    # the lock owner clears a stale request, as run() does (FM36)
    try:
        if not state.autostart:
            session.window.open(url)
            state.opened = True
        while not app.done.is_set():
            session.tick()
            if session.stop.is_set():
                return 0
            if (data_dir / STOP_FILE).exists():   # tools/deploy.py --restart, the installer (FM36)
                remove_stop_file(data_dir / STOP_FILE)
                return 0
            world.sleep(TICK_S)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_dashboard(server)         # the normal run binds the same port next (FM28's shutdown)
    return True


def record_key_status(data_dir: Path, world: ShellWorld, reason: str | None) -> None:
    """Write or clear the Status line api_key_not_saved (FM50); a failure is logged, never fatal.

    Args:
        data_dir: The app's data folder (the database is created when a line must be written).
        world: The clock.
        reason: Why the key wasn't saved, e.g. "permission denied"; None clears the line.
    """
    record_status(data_dir, world, API_KEY_TOPIC, None if reason is None else ("api_key_not_saved", {"reason": reason}))


def record_status(data_dir: Path, world: ShellWorld, topic: str, line: tuple[str, dict] | None) -> None:
    """Write or clear one of the shell's Status lines, all warnings (FM50, FM51, FM56); never fatal.

    Args:
        data_dir: The app's data folder (the database is created when a line must be written).
        world: The clock.
        topic: The line's topic, e.g. "autostart".
        line: (kind, data), e.g. ("autostart_not_set", {"error": "OSError"}); None clears the topic.
    """
    path = data_dir / "ahsync.db"
    if line is None and not path.exists():
        return
    try:
        if path.exists():              # run() may be mid-refresh: no migration, no staging cleanup, wait for locks
            db = open_server_db(path)
        else:                          # before the first run (setup): create it
            db = Database.open(path, world.clock.now_ms(), data_dir / "backups")
    except (DatabaseError, sqlite3.Error, OSError):
        log.warning("could not record the Status line %s", topic)
        return
    try:
        if line is None:
            db.clear_status(topic)
        else:
            with db.transaction():
                db.set_status(topic, "warn", line[0], line[1], world.clock.now_ms())
    except sqlite3.Error:
        log.warning("could not record the Status line %s", topic)
    finally:
        db.close()


def run_until_stopped(session: Session, hooks: AppHooks) -> int:
    """Run AHSync on its own thread and tick on this one until it returns (SPEC §3.7.1).

    Args:
        session: The running session; its stop event is run()'s should_stop.
        hooks: The app shell's dashboard paths (GET /api/app, POST /api/autostart).

    Returns:
        run()'s exit code; 0 when it was stopped by Ctrl-C.
    """
    world, result = session.world, {}
    runner = threading.Thread(
        target=lambda: result.update(code=world.run(session.dirs.data_dir, session.dirs.config_file,
                                                    world.make_runtime(session.stop.is_set, hooks),
                                                    held_lock=session.lock)),
        name="ahsync-run", daemon=True)
    runner.start()
    interrupted = False
    try:
        while runner.is_alive():
            session.tick()
            world.sleep(TICK_S)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        session.stop.set()             # whatever ended the loop, run() returns at its next tick (at most 5 s)
        runner.join()                  # and the lock is released only after that (SPEC §3.7.6)
    return 0 if interrupted else result.get("code", 1)
