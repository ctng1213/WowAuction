"""Windows: %LOCALAPPDATA% and the Windows WoW folders; the Run key and the installer come in M24 and M27 (SPEC §3.7.2)."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

from ahsync_app.platform.base import APP_NAME, WOW_NAMES, AppDirs, Platform

WOW_ROOT = PureWindowsPath(r"C:\Program Files (x86)\World of Warcraft")


class WindowsPlatform(Platform):
    """The Windows answers, over an injected environment and home folder."""

    name = "windows"

    def __init__(self, env: Mapping[str, str], home: Path) -> None:
        """Keep what the answers depend on.

        Args:
            env: Environment variables (LOCALAPPDATA).
            home: The user's profile folder, used when LOCALAPPDATA is not set.
        """
        self.env = env
        self.home = home

    def app_dirs(self) -> AppDirs:
        """`%LOCALAPPDATA%\\AHSync\\config.toml` and `…\\data`: machine-local, not Roaming (SPEC §3.7.3, DR1).

        Returns:
            The two paths.
        """
        local = self.env.get("LOCALAPPDATA") or str(PureWindowsPath(str(self.home)) / "AppData" / "Local")
        root = PureWindowsPath(local) / APP_NAME
        return AppDirs(Path(str(root / "config.toml")), Path(str(root / "data")))

    def wow_candidates(self) -> list[Path]:
        """`C:\\Program Files (x86)\\World of Warcraft\\<name>`, `_classic_beta_` first (SPEC §3.7.3).

        Returns:
            The folders, most likely first.
        """
        return [Path(str(WOW_ROOT / name)) for name in WOW_NAMES]

    def restrict_file(self, path: Path) -> None:
        """Nothing to do: a file in the user's profile is already the user's alone (SPEC §3.7.4).

        Args:
            path: The key file.
        """

    def run_location_problem(self, exe: Path) -> str | None:
        """Windows has no App Translocation: any install folder can start at login (DR11).

        Args:
            exe: The running executable.

        Returns:
            None.
        """
        return None
