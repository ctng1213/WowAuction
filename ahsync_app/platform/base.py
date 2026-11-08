"""The platform protocol: the thirteen narrow questions an OS answers for the app (SPEC §3.7.2).

Everything that decides what AHSync does is shared and tested once; a platform module only
answers these, as data or as a thin writer, over injected fakes so both are tested on any OS.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "AHSync"
WOW_NAMES = ("_forever_", "_classic_beta_")   # the release (seen on the user's Mac 2026-11-06), then the beta's
TRANSLOCATED = "translocated"     # run_location_problem's answer for macOS App Translocation (SPEC §3.7.5)
NOT_SIGNED = "not signed by the AHSync developer"   # verify_update's refusal (SPEC §3.7.9; DR23)


@dataclass(frozen=True)
class Ran:
    """A finished command: its exit code and its output, as text."""

    code: int
    out: str = ""
    err: str = ""


def run_command(args: Sequence[str]) -> Ran:
    """Run a command and wait for it, never through a shell (M27).

    Args:
        args: The program and its arguments.

    Returns:
        Its exit code and output.

    Raises:
        OSError: When the program can't be started.
    """
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=300, check=False)
    return Ran(done.returncode, done.stdout, done.stderr)


def spawn_detached(args: Sequence[str] | str, log: Path | None = None) -> None:
    """Start a process that outlives this one, its output to `log` and never to the app's own (M27).

    Args:
        args: The program and its arguments; on Windows also a whole command line, which `cmd /c` needs verbatim
            (a list would be re-quoted by Python and break cmd's own quoting).
        log: Where its output goes; None discards it.

    Raises:
        OSError: When the program can't be started.
    """
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with (open(log, "ab") if log is not None else open(os.devnull, "wb")) as out:   # noqa: SIM115
        subprocess.Popen(args if isinstance(args, str) else list(args), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=not flags, creationflags=flags, close_fds=True)


@dataclass(frozen=True)
class Processes:
    """How a platform runs commands, injected so tests record them instead (DR22, DR23)."""

    run: Callable[[Sequence[str]], Ran] = lambda args: run_command(args)          # looked up at each call, so a
    spawn: Callable[..., None] = lambda args, log=None: spawn_detached(args, log)   # test guard can forbid them
    pid: int = field(default_factory=os.getpid)


@dataclass(frozen=True)
class AppDirs:
    """Where the packaged app keeps its settings and state (SPEC §3.7.3)."""

    config_file: Path     # <app folder>/config.toml
    data_dir: Path        # <app folder>/data: database, backups, log, lock, stop and show files, updates/
    shown: str = ""       # the app folder as the Status card names it, unexpanded (no user name; M25, D98)


class Platform:
    """The OS layer. Subclasses answer every member."""

    name = "base"

    def app_dirs(self) -> AppDirs:
        """The per-user config file and data folder (DR1).

        Returns:
            The two paths; nothing is created here.
        """
        raise NotImplementedError

    def wow_candidates(self) -> list[Path]:
        """The likely WoW: Forever client folders, most likely first (SPEC §3.7.3).

        Returns:
            Folders that would hold Interface/ and WTF/; none is probed here.
        """
        raise NotImplementedError

    def restrict_file(self, path: Path) -> None:
        """Make a file readable by the user only: the API key file (SPEC §3.7.4).

        Args:
            path: An existing file.

        Raises:
            OSError: When the permissions cannot be changed.
        """
        raise NotImplementedError

    def run_location_problem(self, exe: Path) -> str | None:
        """Whether the app runs from a place Start at login can't point at (SPEC §3.7.5, DR11).

        Args:
            exe: The running executable.

        Returns:
            TRANSLOCATED for a macOS App Translocation path; None when the location is fine.
        """
        raise NotImplementedError

    def autostart_enable(self, exe: Path, args: tuple[str, ...] = ()) -> None:
        """Start AHSync at login with `--autostart`, replacing any older entry (SPEC §3.7.5).

        Args:
            exe: The executable the entry starts.
            args: Arguments before `--autostart`: none for a build; from source, the script to run.

        Raises:
            OSError: When the entry can't be written.
        """
        raise NotImplementedError

    def autostart_disable(self) -> None:
        """Remove the login entry; nothing happens when there is none (SPEC §3.7.5).

        Raises:
            OSError: When the entry can't be removed.
        """
        raise NotImplementedError

    def autostart_enabled(self) -> bool:
        """Whether the login entry exists (SPEC §3.7.5).

        Returns:
            True when AHSync starts at login.
        """
        raise NotImplementedError

    def update_asset_name(self, version: str) -> str:
        """The release asset this OS installs (SPEC §3.7.9).

        Args:
            version: "MAJOR.MINOR.PATCH".

        Returns:
            The asset's file name.
        """
        raise NotImplementedError

    def verify_update(self, path: Path, exe: Path) -> str | None:
        """Check a downloaded update's signature where the OS has one (SPEC §3.7.9; DR23).

        Args:
            path: The downloaded asset, its hash already checked.
            exe: The running executable (macOS compares the download's Team ID with its app's).

        Returns:
            None when it may be installed; else the reason it may not.
        """
        raise NotImplementedError

    def can_replace(self, exe: Path) -> bool:
        """Whether the app can replace itself where it is installed (SPEC §3.7.9; FM54).

        Args:
            exe: The running executable.

        Returns:
            True on Windows (a per-user install); macOS: whether the bundle's folder is writable.
        """
        return True

    def apply_update(self, path: Path, exe: Path) -> None:
        """Start the detached install of a verified update; the shell exits right after (SPEC §3.7.9; DR22, DR23).

        Args:
            path: The verified asset.
            exe: The running executable.

        Raises:
            OSError: When the installer or helper can't be started.
        """
        raise NotImplementedError

    def forget_previous(self, exe: Path) -> None:
        """Remove what an update that worked left behind outside the data folder (macOS: `AHSync.app.previous`).

        Args:
            exe: The running executable, the new version.
        """

    def ca_bundle(self) -> Path | None:
        """The CA certificates TLS uses when the bundled OpenSSL finds none of its own (SPEC §3.7.2, FM58).

        Returns:
            The OS's PEM bundle; None where Python reads the OS's certificate store itself.
        """
        raise NotImplementedError
