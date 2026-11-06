"""The platform protocol: the eleven narrow questions an OS answers for the app (SPEC §3.7.2).

Everything that decides what AHSync does is shared and tested once; a platform module only
answers these, as data or as a thin writer, over injected fakes so both are tested on any OS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APP_NAME = "AHSync"
WOW_NAMES = ("_forever_", "_classic_beta_")   # the release (seen on the user's Mac 2026-11-06), then the beta's
TRANSLOCATED = "translocated"     # run_location_problem's answer for macOS App Translocation (SPEC §3.7.5)


@dataclass(frozen=True)
class AppDirs:
    """Where the packaged app keeps its settings and state (SPEC §3.7.3)."""

    config_file: Path     # <app folder>/config.toml
    data_dir: Path        # <app folder>/data: database, backups, log, lock, stop and show files, updates/
    shown: str = ""       # the app folder as the Status card names it, unexpanded (no user name; M25, D98)


class Platform:
    """The OS layer. Subclasses answer every member; the update members (M27) raise NotImplementedError
    until their milestone builds them."""

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

    def verify_update(self, path: Path) -> str | None:
        """Check a downloaded update's signature where the OS has one (SPEC §3.7.9).

        Args:
            path: The downloaded asset, its hash already checked.

        Returns:
            None when it may be installed; else the reason it may not.
        """
        raise NotImplementedError("automatic install is built in M27")

    def apply_update(self, path: Path, exe: Path) -> None:
        """Start the detached install of a verified update; the shell exits right after (SPEC §3.7.9).

        Args:
            path: The verified asset.
            exe: The running executable.
        """
        raise NotImplementedError("automatic install is built in M27")

    def ca_bundle(self) -> Path | None:
        """The CA certificates TLS uses when the bundled OpenSSL finds none of its own (SPEC §3.7.2, FM58).

        Returns:
            The OS's PEM bundle; None where Python reads the OS's certificate store itself.
        """
        raise NotImplementedError
