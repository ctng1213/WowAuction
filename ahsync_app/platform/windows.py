"""Windows: %LOCALAPPDATA%, the Windows WoW folders and the Run key; the installer comes in M27 (SPEC §3.7.2)."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Protocol

from ahsync_app.platform.base import APP_NAME, WOW_NAMES, AppDirs, Platform

WOW_ROOT = PureWindowsPath(r"C:\Program Files (x86)\World of Warcraft")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"   # under HKEY_CURRENT_USER (SPEC §3.7.5)
RUN_VALUE = APP_NAME


class RunKey(Protocol):
    """The current user's Run key, as the three operations start at login needs (a fake in tests)."""

    def get(self, name: str) -> str | None:
        """Read a value.

        Args:
            name: The value's name.

        Returns:
            Its text; None when it is missing.
        """

    def set(self, name: str, data: str) -> None:
        """Write a text value, replacing any old one.

        Args:
            name: The value's name.
            data: Its text.
        """

    def delete(self, name: str) -> None:
        """Remove a value; nothing happens when it is missing.

        Args:
            name: The value's name.
        """


class WinRegRunKey:
    """`HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` through winreg (imported on use: macOS has none)."""

    def get(self, name: str) -> str | None:
        """Read a value of the Run key.

        Args:
            name: The value's name.

        Returns:
            Its text; None when the value (or the key) is missing.

        Raises:
            OSError: When the key can't be read.
        """
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except FileNotFoundError:
            return None

    def set(self, name: str, data: str) -> None:
        """Write a REG_SZ value, creating the key when needed.

        Args:
            name: The value's name.
            data: Its text.

        Raises:
            OSError: When the key can't be written.
        """
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, data)

    def delete(self, name: str) -> None:
        """Remove a value; a missing one is fine.

        Args:
            name: The value's name.

        Raises:
            OSError: When the key can't be written.
        """
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


class WindowsPlatform(Platform):
    """The Windows answers, over an injected environment and home folder."""

    name = "windows"

    def __init__(self, env: Mapping[str, str], home: Path, run_key: RunKey | None = None) -> None:
        """Keep what the answers depend on.

        Args:
            env: Environment variables (LOCALAPPDATA).
            home: The user's profile folder, used when LOCALAPPDATA is not set.
            run_key: The Run key; None uses the real one through winreg.
        """
        self.env = env
        self.home = home
        self.run_key = run_key if run_key is not None else WinRegRunKey()

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

    def autostart_enable(self, exe: Path, args: tuple[str, ...] = ()) -> None:
        """Write the Run value `AHSync` = `"<exe>" --autostart`; a second enable replaces it (SPEC §3.7.5, DR9).

        Args:
            exe: The executable the login entry starts.
            args: Arguments before --autostart, each quoted (from source: the script).

        Raises:
            OSError: When the Run key can't be written.
        """
        self.run_key.set(RUN_VALUE, " ".join(f'"{part}"' for part in (exe, *args)) + " --autostart")

    def autostart_disable(self) -> None:
        """Remove the Run value; nothing happens when there is none (DR9).

        Raises:
            OSError: When the Run key can't be written.
        """
        self.run_key.delete(RUN_VALUE)

    def autostart_enabled(self) -> bool:
        """Whether the Run value exists (DR9).

        Returns:
            True when AHSync starts at login.

        Raises:
            OSError: When the Run key can't be read.
        """
        return self.run_key.get(RUN_VALUE) is not None
