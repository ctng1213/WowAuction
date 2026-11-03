"""Start the AHSync app: `python -m ahsync_app [--autostart]`, or the packaged AHSync (SPEC §3.7.1).

Runs against the per-user folders (SPEC §3.7.3), unlike `python -m ahsync`, which keeps
./data and ./config.toml.
"""
from __future__ import annotations

import argparse
import io
import os
import socket
import ssl
import sys
import time
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path

# A PyInstaller build also runs this file as a script; it must keep its own sys.path.
if not __package__ and not getattr(sys, "frozen", False):   # from source as a script (the login entry): find the repository's packages,
    _here = Path(__file__).resolve().parent   # and drop this folder, whose platform/ would shadow the stdlib module
    sys.path[:] = [str(_here.parent)] + [p for p in sys.path if Path(p or ".").resolve() != _here]

from ahsync.__main__ import Runtime, run
from ahsync.clock import SystemClock
from ahsync.collect.client import UrllibTransport
from ahsync.web.server import AppHooks
from ahsync_app import platform
from ahsync_app.shell import ShellWorld, start
from ahsync_app.tray import PystrayIcon
from ahsync_app.window import PyWebview

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


def login_command(frozen: bool, executable: Path, script: Path) -> tuple[Path, tuple[str, ...]]:
    """What the login entry starts (SPEC §3.7.5): the app itself, or from source pythonw with this script.

    Args:
        frozen: Whether this is a PyInstaller build (`sys.frozen`).
        executable: `sys.executable`.
        script: This file, which runs the app from any working folder.

    Returns:
        (executable, arguments before --autostart): (AHSync.exe, ()) for a build; from source pythonw.exe beside
        python.exe when there is one (no console window at login), and the script.
    """
    if frozen:
        return executable, ()
    windowless = executable.with_name("pythonw.exe")
    return (windowless if windowless.exists() else executable), (str(script),)


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


def tls_ca_env(env: Mapping[str, str], defaults: ssl.DefaultVerifyPaths, bundle: Path | None) -> dict[str, str]:
    """Where TLS finds CA certificates when the bundled OpenSSL has none (FM58).

    A build's OpenSSL looks in the build machine's Python folder; without certificates every HTTPS call fails.

    Args:
        env: The process environment.
        defaults: `ssl.get_default_verify_paths()`; its cafile is None when that file doesn't exist.
        bundle: The platform's CA bundle (`Platform.ca_bundle()`); None where Python reads the OS's store.

    Returns:
        {"SSL_CERT_FILE": bundle} when OpenSSL's default CA file is missing, the user set none and the bundle
        exists; else {} (the environment is left alone).
    """
    if bundle is None or env.get("SSL_CERT_FILE") or defaults.cafile is not None or not bundle.is_file():
        return {}
    return {"SSL_CERT_FILE": str(bundle)}


def real_world(out: io.TextIOBase | None) -> ShellWorld:
    """The shell's outside world when it really runs.

    Args:
        out: Where start-up messages go (stderr); None in a windowed build, which has no console.

    Returns:
        The world over the current platform, the real run(), the window (pywebview), the tray (pystray),
        the browser and the clock.
    """
    stream = out if out is not None else io.StringIO()
    env, home = dict(os.environ), Path.home()

    def make_runtime(should_stop: Callable[[], bool], hooks: AppHooks) -> Runtime:
        """The Runtime run() gets, polling the shell's stop event.

        Args:
            should_stop: The shell's stop event's is_set.
            hooks: The app shell's paths for run()'s dashboard (GET /api/app, POST /api/autostart).

        Returns:
            The real environment, transport and clock.
        """
        return Runtime(env, home, UrllibTransport(), SystemClock(), should_stop, stream, app_hooks=hooks)

    bundle = bundle_addon_dir(bool(getattr(sys, "frozen", False)), getattr(sys, "_MEIPASS", None),
                              Path(__file__).resolve().parents[1])
    exe, launch_args = login_command(bool(getattr(sys, "frozen", False)), Path(sys.executable), Path(__file__).resolve())
    return ShellWorld(platform.current(), run, make_runtime, webbrowser.open, port_open, time.sleep, bundle,
                      env, home, stream, SystemClock(), exe, launch_args=launch_args,
                      webview=PyWebview, make_tray=PystrayIcon)


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
    os.environ.update(tls_ca_env(os.environ, ssl.get_default_verify_paths(), platform.current().ca_bundle()))
    return start(args.autostart, real_world(sys.stderr))


if __name__ == "__main__":
    sys.exit(main())
