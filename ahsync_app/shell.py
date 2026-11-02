"""The app's lifecycle around `ahsync.run()` (SPEC §3.7.1, §3.7.6, §3.7.7).

At each start, in order: take the instance lock (a second launch hands over to the running
one through `<data>/ahsync.show` and exits); when `config.toml` is missing, serve the setup page
until it is saved (the save installs the addon); otherwise install the bundled addon; run AHSync
on a worker thread; and, on the main thread, tick once a second until it stops: remove a show
file (the window shows from M24), and on a manual launch open the dashboard once it answers.
Ctrl-C or the stop file ends the run; the lock is released only after `run()` has returned.

Built without the tray and the window (M24): the opener is the browser.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import IO

from ahsync.__main__ import (API_KEY_TOPIC, LOCK_FILE, STOP_FILE, InstanceLock, Runtime, remove_stop_file,
                             setup_logging)
from ahsync.collect.client import Redactor
from ahsync.app.setup import SetupRequest, save_setup
from ahsync.app.wow_paths import detect
from ahsync.clock import Clock
from ahsync.config import Config, ConfigError, load_config, resolve_api_key
from ahsync.db.sqlite import Database, DatabaseError
from ahsync.export.addon_install import AddonResult, install_addon
from ahsync.web.server import new_token, start_dashboard, stop_dashboard
from ahsync.web.setup_app import SetupApp
from ahsync_app.app_info import app_payload
from ahsync_app.platform.base import AppDirs, Platform

log = logging.getLogger("ahsync.app")

SHOW_FILE = "ahsync.show"     # in data/: a second launch asks the running shell to show its window (FM48)
TICK_S = 1.0
NO_SETUP = 2                  # exit code when the setup page can't be served (its port is taken)


@dataclass
class ShellState:
    """What the running shell knows about itself; the window, the tray and `GET /api/app` read it (M23, M24)."""

    autostart: bool                    # launched by the login entry: no window, no browser
    addon: AddonResult | None = None   # this start's addon install (setup's, after a setup); None: none yet
    opened: bool = False               # the dashboard has been opened for this launch
    shows: int = 0                     # show requests handled (second launches)


@dataclass(frozen=True)
class ShellWorld:
    """What the shell gets from the outside world, so tests replace all of it."""

    platform: Platform
    run: Callable[..., int]                                  # ahsync.__main__.run
    make_runtime: Callable[[Callable[[], bool], Callable[[], dict]], Runtime]   # (should_stop, app_info) -> Runtime
    opener: Callable[[str], object]                          # opens a URL (the browser until M24)
    port_open: Callable[[int], bool]                         # whether the dashboard answers on localhost:<port>
    sleep: Callable[[float], None]
    bundle_addon_dir: Path                                   # the addon/ folder shipped with the app
    env: dict
    home: Path
    out: IO[str]
    clock: Clock                                             # for the Status line setup may write (FM50)
    exe: Path                                                # the running executable (start at login, M24)
    setup_port: int = Config().dashboard_port                # setup's port: config.toml's default (no config yet)


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


def start(autostart: bool, world: ShellWorld) -> int:
    """Run the app from launch to quit (SPEC §3.7.1; DR1, DR3, DR5, FM48, FM49).

    Args:
        autostart: Launched with --autostart (by the login entry): nothing is opened.
        world: The outside world.

    Returns:
        0 after a hand-over to the running app, a normal stop or a setup left with Ctrl-C; NO_SETUP when
        the setup page can't be served; else run()'s exit code.

    Side effects:
        Creates the per-user folders; installs the addon; writes the show file when another
        instance holds the lock (not on an --autostart launch).
    """
    dirs = world.platform.app_dirs()
    dirs.data_dir.mkdir(parents=True, exist_ok=True)
    dirs.config_file.parent.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(dirs.data_dir / LOCK_FILE)
    if not lock.acquire():
        if not autostart:              # the login entry starts at once when turned on: stay silent (FM48)
            request_show(dirs.data_dir)
        return 0
    try:
        state = ShellState(autostart)
        if not dirs.config_file.exists():   # only a missing config: an existing one is never rewritten (DR5)
            saved = serve_setup(dirs, world.setup_port, state, world)
            if saved is not True:
                return saved
        config = load_settings(dirs.config_file, world)
        if config is not None and state.addon is None:   # only the lock owner installs (SPEC §3.7.6)
            state.addon = install_addon(world.bundle_addon_dir, config.wow_path)
        if state.addon is not None:
            print(f"Addon: {state.addon.line['text']}", file=world.out)
        port = (config or Config()).dashboard_port
        info = partial(app_payload, world, state, False, config.wow_path if config else "",
                       bool(config and config.api_key))
        return run_until_stopped(dirs.data_dir, dirs.config_file, lock, port, state, world, info)
    finally:
        lock.release()


def serve_setup(dirs: AppDirs, port: int, state: ShellState, world: ShellWorld) -> bool | int:
    """Serve the setup page until it is saved, then stop it for the normal run (SPEC §3.7.4; DR5, DR7, FM50).

    Args:
        dirs: The per-user folders.
        port: The dashboard's default port (there is no config yet).
        state: Gets the addon install the save ran.
        world: The outside world.

    Returns:
        True after a save; 0 when Ctrl-C ended it; NO_SETUP when the port can't be bound.
    """
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
            enable_autostart(world)
        state.addon = install_addon(world.bundle_addon_dir, request.wow_path)
        record_key_status(dirs.data_dir, world, None)
        return state.addon.restart_wow

    key_set = resolve_api_key(world.env, world.home, "") != ""
    app = SetupApp(new_token(), port, partial(app_payload, world, state, True, wow, key_set),
                   partial(save_setup, config_file=dirs.config_file, home=world.home,
                           restrict_file=world.platform.restrict_file),
                   after_save, partial(record_key_status, dirs.data_dir, world))
    redactor = Redactor()
    app.redact = redactor.add
    handler = setup_logging(dirs.data_dir, redactor)   # setup's log lines go to data/ahsync.log, keys scrubbed
    try:
        return wait_for_setup(app, dirs.data_dir, port, state, world)
    finally:
        logging.getLogger("ahsync").removeHandler(handler)
        handler.close()


def wait_for_setup(app: SetupApp, data_dir: Path, port: int, state: ShellState, world: ShellWorld) -> bool | int:
    """Serve setup until it is saved, Ctrl-C or the stop file (SPEC §3.7.4).

    Args:
        app: The setup requests.
        data_dir: The app's data folder (show and stop files).
        port: Setup's port.
        state: Updated with what was opened and shown.
        world: The outside world.

    Returns:
        True after a save; 0 after Ctrl-C or the stop file; NO_SETUP when the port can't be bound.
    """
    try:
        server = start_dashboard(app, port)
    except OSError:
        print(f"Setup not started: port {port} is not available", file=world.out)
        return NO_SETUP
    url = f"http://localhost:{port}/setup"
    print(f"Setup: {url}", file=world.out)
    remove_stop_file(data_dir / STOP_FILE)    # the lock owner clears a stale request, as run() does (FM36)
    try:
        if not state.autostart:
            world.opener(url)
            state.opened = True
        while not app.done.is_set():
            if take_show_request(data_dir):
                state.shows += 1
            if (data_dir / STOP_FILE).exists():   # tools/deploy.py --restart, the installer (FM36)
                remove_stop_file(data_dir / STOP_FILE)
                return 0
            world.sleep(TICK_S)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_dashboard(server)         # the normal run binds the same port next (FM28's shutdown)
    return True


def enable_autostart(world: ShellWorld) -> None:
    """Turn start at login on after setup asked for it (SPEC §3.7.5); a failure is logged, never fatal.

    Args:
        world: The platform and the executable.
    """
    try:
        world.platform.autostart_enable(world.exe)
    except NotImplementedError:
        log.info("start at login is not built yet (M24)")
    except Exception as error:         # an OS writer's failure (the login entry) never stops the setup
        log.warning("start at login not set: %s", type(error).__name__)


def record_key_status(data_dir: Path, world: ShellWorld, reason: str | None) -> None:
    """Write or clear the Status line api_key_not_saved (FM50); a failure is logged, never fatal.

    Args:
        data_dir: The app's data folder (the database is created when a line must be written).
        world: The clock.
        reason: Why the key wasn't saved, e.g. "permission denied"; None clears the line.
    """
    path = data_dir / "ahsync.db"
    if reason is None and not path.exists():
        return
    try:
        db = Database.open(path, world.clock.now_ms(), data_dir / "backups")
    except (DatabaseError, sqlite3.Error, OSError):
        log.warning("could not record the API key's Status line")
        return
    try:
        if reason is None:
            db.clear_status(API_KEY_TOPIC)
        else:
            with db.transaction():
                db.set_status(API_KEY_TOPIC, "warn", "api_key_not_saved", {"reason": reason}, world.clock.now_ms())
    except sqlite3.Error:
        log.warning("could not record the API key's Status line")
    finally:
        db.close()


def run_until_stopped(data_dir: Path, config_file: Path, lock: InstanceLock, port: int, state: ShellState,
                      world: ShellWorld, app_info: Callable[[], dict]) -> int:
    """Run AHSync on a worker thread and tick on this one until it returns (SPEC §3.7.1).

    Args:
        data_dir: The app's data folder.
        config_file: config.toml.
        lock: The instance lock this shell holds; run() uses it and leaves its release to start().
        port: The dashboard's port.
        state: The shell's state, updated by each tick.
        world: The outside world.
        app_info: GET /api/app's payload, served by run()'s dashboard.

    Returns:
        run()'s exit code; 0 when it was stopped by Ctrl-C.
    """
    stop = threading.Event()
    result: dict[str, int] = {}
    worker = threading.Thread(
        target=lambda: result.update(code=world.run(data_dir, config_file, world.make_runtime(stop.is_set, app_info),
                                                    held_lock=lock)),
        name="ahsync-run", daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            tick(data_dir, port, state, world)
            world.sleep(TICK_S)
    except KeyboardInterrupt:
        stop.set()                     # run() returns at its next tick (at most 5 s)
        worker.join()
        return 0
    worker.join()
    return result.get("code", 1)


def tick(data_dir: Path, port: int, state: ShellState, world: ShellWorld) -> None:
    """One pass of the shell's loop: a show request, and the first open of a manual launch.

    Args:
        data_dir: The app's data folder.
        port: The dashboard's port.
        state: Updated with what was handled.
        world: The outside world.
    """
    if take_show_request(data_dir):
        state.shows += 1               # M24 shows the window here; the browser opens only once per launch
    if not state.autostart and not state.opened and world.port_open(port):
        world.opener(f"http://localhost:{port}")
        state.opened = True
