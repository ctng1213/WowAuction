"""The app's lifecycle around `ahsync.run()` (SPEC §3.7.1, §3.7.6, §3.7.7).

At each start, in order: take the instance lock (a second launch hands over to the running
one through `<data>/ahsync.show` and exits); install the bundled addon; run AHSync on a worker
thread; and, on the main thread, tick once a second until it stops: remove a show file (the
window shows from M24), and on a manual launch open the dashboard once it answers. Ctrl-C or
the stop file ends the run; the lock is released only after `run()` has returned.

Built in M22 without the tray and the window: the opener is the browser. Setup mode for a
missing config.toml comes in M23; until then the shell refuses to start without one (`run()`
alone would run on the defaults, SPEC §3.6, which setup exists to replace).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ahsync.__main__ import LOCK_FILE, InstanceLock, Runtime
from ahsync.config import Config, ConfigError, load_config
from ahsync.export.addon_install import AddonResult, install_addon
from ahsync_app.platform.base import Platform

SHOW_FILE = "ahsync.show"     # in data/: a second launch asks the running shell to show its window (FM48)
TICK_S = 1.0
NO_CONFIG = 2                 # exit code without config.toml, run()'s code for a bad config


@dataclass
class ShellState:
    """What the running shell knows about itself; the window, the tray and `GET /api/app` read it (M23, M24)."""

    autostart: bool                    # launched by the login entry: no window, no browser
    addon: AddonResult | None = None   # this start's addon install; None when the config is invalid
    opened: bool = False               # the dashboard has been opened for this launch
    shows: int = 0                     # show requests handled (second launches)


@dataclass(frozen=True)
class ShellWorld:
    """What the shell gets from the outside world, so tests replace all of it."""

    platform: Platform
    run: Callable[..., int]                                  # ahsync.__main__.run
    make_runtime: Callable[[Callable[[], bool]], Runtime]    # should_stop -> the Runtime run() gets
    opener: Callable[[str], object]                          # opens a URL (the browser until M24)
    port_open: Callable[[int], bool]                         # whether the dashboard answers on localhost:<port>
    sleep: Callable[[float], None]
    bundle_addon_dir: Path                                   # the addon/ folder shipped with the app
    env: dict
    home: Path
    out: IO[str]


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
    """Run the app from launch to quit (SPEC §3.7.1; DR1, DR3, FM48, FM49).

    Args:
        autostart: Launched with --autostart (by the login entry): nothing is opened.
        world: The outside world.

    Returns:
        0 after a hand-over to the running app or a normal stop; NO_CONFIG without config.toml;
        else run()'s exit code.

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
        if not dirs.config_file.exists():   # M23 serves the setup page here instead
            print(f"AHSync can't start: no {dirs.config_file}; copy docs/config.toml there (first-run setup "
                  "comes in a later version)", file=world.out)
            return NO_CONFIG
        state = ShellState(autostart)
        config = load_settings(dirs.config_file, world)
        if config is not None:         # only the lock owner installs, so two shells never write the same files
            state.addon = install_addon(world.bundle_addon_dir, config.wow_path)
            print(f"Addon: {state.addon.line['text']}", file=world.out)
        port = (config or Config()).dashboard_port
        return run_until_stopped(dirs.data_dir, dirs.config_file, lock, port, state, world)
    finally:
        lock.release()


def run_until_stopped(data_dir: Path, config_file: Path, lock: InstanceLock, port: int, state: ShellState,
                      world: ShellWorld) -> int:
    """Run AHSync on a worker thread and tick on this one until it returns (SPEC §3.7.1).

    Args:
        data_dir: The app's data folder.
        config_file: config.toml.
        lock: The instance lock this shell holds; run() uses it and leaves its release to start().
        port: The dashboard's port.
        state: The shell's state, updated by each tick.
        world: The outside world.

    Returns:
        run()'s exit code; 0 when it was stopped by Ctrl-C.
    """
    stop = threading.Event()
    result: dict[str, int] = {}
    worker = threading.Thread(
        target=lambda: result.update(code=world.run(data_dir, config_file, world.make_runtime(stop.is_set),
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
