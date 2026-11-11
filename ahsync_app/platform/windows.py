"""Windows: %LOCALAPPDATA%, the Windows WoW folders, the Run key and the silent upgrade (SPEC §3.7.2, §3.7.9)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path, PureWindowsPath
from typing import Protocol

from ahsync.engine.updates import BRIDGE_IN_USE
from ahsync_app.platform.base import APP_NAME, WOW_NAMES, AppDirs, Platform, Processes

# Battle.net installs under either root: Program Files (x86) for older installs, Program Files for newer ones.
WOW_ROOTS = (PureWindowsPath(r"C:\Program Files (x86)\World of Warcraft"), PureWindowsPath(r"C:\Program Files\World of Warcraft"))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"   # under HKEY_CURRENT_USER (SPEC §3.7.5)
RUN_VALUE = APP_NAME
# Inno Setup's silent upgrade (DR22). /NOCLOSEAPPLICATIONS keeps the script's CloseApplications=no: its [Code] stops
# AHSync through the stop file, since Restart Manager would hang on the window cancelling its close.
SILENT = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOCLOSEAPPLICATIONS"
BRIDGE_EXE = "ahsync-mcp.exe"     # the AI assistant bridge next to AHSync.exe (SPEC §3.8.2)


def file_in_use(path: Path) -> bool:
    """Whether a program file is running, so nothing can replace it (FM87).

    Windows refuses write access to the file of a running program (a sharing violation); opening it for read and
    write, without writing, finds that out. The file is never changed.

    Args:
        path: The program file.

    Returns:
        True when write access is refused; False when it is granted, or the file is missing or unreadable for
        another reason (the installer's own check, FM82, still stands behind this one).
    """
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


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

    def __init__(self, env: Mapping[str, str], home: Path, run_key: RunKey | None = None,
                 processes: Processes | None = None, in_use: Callable[[Path], bool] = file_in_use) -> None:
        """Keep what the answers depend on.

        Args:
            env: Environment variables (LOCALAPPDATA).
            home: The user's profile folder, used when LOCALAPPDATA is not set.
            run_key: The Run key; None uses the real one through winreg.
            processes: Starts the installer (the real one by default; a recorder in tests).
            in_use: Whether a program file is running (`file_in_use`; a fake in tests).
        """
        self.env = env
        self.home = home
        self.run_key = run_key if run_key is not None else WinRegRunKey()
        self.processes = processes or Processes()
        self.in_use = in_use

    def bridge_command(self, exe: Path) -> tuple[str, ...]:
        """`ahsync-mcp.exe`, the console bridge installed next to AHSync.exe: the app is windowed, so it has no
        stdin or stdout of its own (SPEC §3.8.2).

        Args:
            exe: AHSync.exe.

        Returns:
            (the bridge's full path,).
        """
        return (str(PureWindowsPath(str(exe)).parent / BRIDGE_EXE),)

    def update_blocker(self, exe: Path) -> str | None:
        """Whether the installer would refuse now: it can't replace the bridge while Claude runs it (FM82, FM87).

        Args:
            exe: AHSync.exe; the bridge is installed next to it.

        Returns:
            BRIDGE_IN_USE while `ahsync-mcp.exe` runs; else None.
        """
        return BRIDGE_IN_USE if self.in_use(exe.parent / BRIDGE_EXE) else None

    def app_dirs(self) -> AppDirs:
        """`%LOCALAPPDATA%\\AHSync\\config.toml` and `…\\data`: machine-local, not Roaming (SPEC §3.7.3, DR1).

        Returns:
            The two paths.
        """
        local = self.env.get("LOCALAPPDATA") or str(PureWindowsPath(str(self.home)) / "AppData" / "Local")
        root = PureWindowsPath(local) / APP_NAME
        return AppDirs(Path(str(root / "config.toml")), Path(str(root / "data")), rf"%LOCALAPPDATA%\{APP_NAME}")

    def wow_candidates(self) -> list[Path]:
        """`<root>\\World of Warcraft\\<name>` under both Program Files roots, `_forever_` first (SPEC §3.7.3).

        Returns:
            The folders, most likely first.
        """
        return [Path(str(root / name)) for name in WOW_NAMES for root in WOW_ROOTS]

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

    def update_asset_name(self, version: str) -> str:
        """The installer this OS takes from a release (SPEC §3.7.9; DR20).

        Args:
            version: "MAJOR.MINOR.PATCH".

        Returns:
            "AHSync-<version>-windows-x64-setup.exe".
        """
        return f"{APP_NAME}-{version}-windows-x64-setup.exe"

    def verify_update(self, path: Path, exe: Path) -> str | None:
        """Nothing beyond the hash: the installer is unsigned (SPEC §3.7.9).

        Args:
            path: The downloaded installer, its hash checked.
            exe: The running executable (unused here).

        Returns:
            None.
        """
        return None

    def apply_update(self, path: Path, exe: Path) -> None:
        """Start the silent installer detached, relaunching the old app when it exits non-zero (DR22, FM55).

        Inno Setup upgrades the per-user install in place and its [Run] entry starts the new AHSync after a silent
        install; when the installer fails, `cmd`'s `||` starts the old one, so AHSync is running either way.

        Args:
            path: The verified `AHSync-<ver>-windows-x64-setup.exe` in `<data>/updates/`.
            exe: The running `AHSync.exe`.

        Raises:
            OSError: When a path holds `%` or `"` (cmd would expand or end it, even inside quotes) or `cmd` can't start.
        """
        if any(c in str(p) for p in (path, exe) for c in '%"'):
            raise OSError("an install path holds % or \"; install it from the release page")
        # /s: cmd strips exactly the outer quotes of `/c "…"`, leaving the quoted paths intact (spaces in a user
        # name); /d: no AutoRun commands.
        self.processes.spawn(f'cmd /d /s /c ""{path}" {SILENT} || start "" "{exe}""', path.parent / "install.log")

    def ca_bundle(self) -> Path | None:
        """None: Python's `ssl` loads the Windows certificate store itself (FM58).

        Returns:
            None.
        """
        return None
