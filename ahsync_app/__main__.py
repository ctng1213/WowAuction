"""Start the AHSync app: `python -m ahsync_app [--autostart]`, or the packaged AHSync (SPEC §3.7.1).

Runs against the per-user folders (SPEC §3.7.3), unlike `python -m ahsync`, which keeps
./data and ./config.toml.
"""
from __future__ import annotations

import argparse
import io
import os
import socket
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from ahsync.__main__ import Runtime, run
from ahsync.clock import SystemClock
from ahsync.collect.client import UrllibTransport
from ahsync_app import platform
from ahsync_app.shell import ShellWorld, start

PORT_PROBE_S = 0.5


def bundle_addon_dir(frozen: bool, meipass: str | None, source_root: Path) -> Path:
    """The addon folder shipped with the app: the one place that knows source from frozen (SPEC §3.7.7).

    Args:
        frozen: Whether this is a PyInstaller build (`sys.frozen`).
        meipass: PyInstaller's unpacked bundle folder (`sys._MEIPASS`), when frozen.
        source_root: The repository root, from source.

    Returns:
        `<bundle>/addon` when frozen, else `<repo>/addon`.
    """
    return Path(meipass) / "addon" if frozen and meipass else source_root / "addon"


def port_open(port: int) -> bool:
    """Whether something answers on localhost:<port>: the dashboard is up.

    Args:
        port: The dashboard's port.

    Returns:
        True when a TCP connection succeeds within PORT_PROBE_S.
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=PORT_PROBE_S):
            return True
    except OSError:
        return False


def real_world(out: io.TextIOBase | None) -> ShellWorld:
    """The shell's outside world when it really runs.

    Args:
        out: Where start-up messages go (stderr); None in a windowed build, which has no console.

    Returns:
        The world over the current platform, the real run(), the browser and the clock.
    """
    stream = out if out is not None else io.StringIO()
    env, home = dict(os.environ), Path.home()

    def make_runtime(should_stop: Callable[[], bool]) -> Runtime:
        """The Runtime run() gets, polling the shell's stop event.

        Args:
            should_stop: The shell's stop event's is_set.

        Returns:
            The real environment, transport and clock.
        """
        return Runtime(env, home, UrllibTransport(), SystemClock(), should_stop, stream)

    bundle = bundle_addon_dir(bool(getattr(sys, "frozen", False)), getattr(sys, "_MEIPASS", None),
                              Path(__file__).resolve().parents[1])
    return ShellWorld(platform.current(), run, make_runtime, webbrowser.open, port_open, time.sleep, bundle,
                      env, home, stream)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; None reads sys.argv.

    Returns:
        The shell's exit code.
    """
    parser = argparse.ArgumentParser(prog="ahsync_app", description="AHSync, the app: tray, window, setup")
    parser.add_argument("--autostart", action="store_true", help="started at login: open nothing")
    args = parser.parse_args(argv)
    return start(args.autostart, real_world(sys.stderr))


if __name__ == "__main__":
    sys.exit(main())
